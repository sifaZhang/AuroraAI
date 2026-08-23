from __future__ import annotations

import argparse
from pathlib import Path
from datetime import date
from typing import Any

import pandas as pd

from .fundamental_analyst import AnalystInputError
from .json_store import read_json, write_json_atomic
from .profile_builder import DEFAULT_RESEARCH_ROOT
from .valuation_metrics import fcf, percentile, ratio
from .valuation_repository import TushareValuationProvider, ValuationDataError


def _latest_by_year(frame: pd.DataFrame, year: str) -> dict[str, Any] | None:
    if frame is None or frame.empty or "end_date" not in frame:
        return None
    rows = frame[frame["end_date"].astype(str).eq(f"{year}1231")].sort_values([key for key in ("ann_date", "end_date") if key in frame], ascending=False)
    return rows.iloc[0].to_dict() if not rows.empty else None


def _number(row: dict[str, Any] | None, key: str) -> float | None:
    if not row or key not in row or pd.isna(row[key]): return None
    try: return float(row[key])
    except (TypeError, ValueError): return None


def _fact(value: float | None, source: str, *, evidence_id: str | None = None, formula: str | None = None, inputs: list[str] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"value": value, "source_type": source, "derived": formula is not None}
    if evidence_id: item["evidence_id"] = evidence_id
    if formula: item.update(formula=formula, inputs=inputs or [])
    return item


class ValuationInputBuilder:
    def __init__(self, *, provider: TushareValuationProvider | None = None, research_root: Path = DEFAULT_RESEARCH_ROOT) -> None:
        self.provider, self.research_root = provider, research_root

    def build(
        self,
        symbol: str,
        *,
        version: str | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        folder, actual = self._folder(symbol, version); output = folder / "valuation_input.json"
        if output.exists() and not force and not dry_run:
            raise FileExistsError(f"valuation_input.json already exists for {actual}; use --force")
        snapshot, evidence, company = (read_json(path) for path in (folder/"input_snapshot.json", folder/"evidence.json", folder.parents[1]/"company.json"))
        if snapshot.get("symbol") != symbol.upper() or not company: raise AnalystInputError("company or input snapshot missing")
        provider = self.provider or TushareValuationProvider(); source = provider.data(symbol)
        annuals=[]; dividends={item["calendar_year"]:item for item in snapshot.get("dividend_history", [])}
        for year in map(str, range(date.today().year - 5, date.today().year)):
            income,cash,balance,indicator=(_latest_by_year(source[key],year) for key in ("income","cashflow","balance","indicator"))
            operating,capex= _number(cash,"n_cashflow_act"),_number(cash,"c_pay_acq_const_fiolta")
            profit,eps,shares=_number(income,"n_income_attr_p"),_number(income,"basic_eps"),_number(balance,"total_share")
            dps=dividends.get(int(year),{}).get("current_basis_dps") or dividends.get(int(year),{}).get("cash_dividend_per_share")
            total_dividend = dps * shares if dps is not None and shares is not None else None
            annuals.append({"period":year,"revenue":_fact(_number(income,"total_revenue"),"tushare_api"),"net_profit_parent":_fact(profit,"tushare_api"),"adjusted_net_profit":_fact(_number(income,"profit_dedt"),"tushare_api"),"operating_cash_flow":_fact(operating,"tushare_api"),"capex":_fact(capex,"tushare_api"),"free_cash_flow":_fact(fcf(operating,capex),"derived",formula="operating_cash_flow_minus_capex",inputs=["operating_cash_flow","capex"]),"roe":_fact(_number(indicator,"roe"),"tushare_api"),"eps":_fact(eps,"tushare_api"),"cash_dividend_total":_fact(total_dividend,"derived",formula="dps_times_total_share",inputs=["dividend_dps","shares_outstanding"]),"payout_ratio":_fact(ratio(total_dividend,profit),"derived",formula="cash_dividend_total_div_net_profit_parent",inputs=["cash_dividend_total","net_profit_parent"]),"dividend_to_fcf_ratio":_fact(ratio(total_dividend,fcf(operating,capex)),"derived",formula="cash_dividend_total_div_free_cash_flow",inputs=["cash_dividend_total","free_cash_flow"])})
        daily = source["daily_basic"].sort_values("trade_date", ascending=False) if not source["daily_basic"].empty else source["daily_basic"]
        latest = daily.iloc[0].to_dict() if not daily.empty else {}
        balance_latest = _latest_by_year(source["balance"], str(date.today().year - 1))
        price=snapshot.get("yield_context",{}).get("latest_price") or _number(latest,"close")
        shares=_number(balance_latest,"total_share")
        debt_values = [_number(balance_latest, key) for key in ("st_borr", "lt_borr", "bonds_pay", "non_cur_liab_due_1y")]
        debt = sum(value or 0 for value in debt_values) if any(value is not None for value in debt_values) else None
        cash = _number(balance_latest,"money_cap")
        current_pe,current_pb=_number(latest,"pe_ttm"),_number(latest,"pb")
        missing = ["historical market data limited to Tushare daily_basic availability; no hidden winsorization"]
        for item in annuals:
            for field in ("revenue", "net_profit_parent", "adjusted_net_profit", "operating_cash_flow", "capex", "free_cash_flow", "roe", "eps"):
                if item[field]["value"] is None:
                    missing.append(f"{item['period']} {field} unavailable")
        input_data={"schema_version":1,"symbol":symbol.upper(),"analysis_version":actual,"as_of":snapshot.get("as_of"),"research_type":company.get("research_type",{}),"market":{"latest_price":_fact(price,"dividend_snapshot",evidence_id=next((x["id"] for x in evidence.get("evidence",[]) if x["id"].startswith("dividend_snapshot:")),None)),"market_cap":_fact(_number(latest,"total_mv")*10000 if _number(latest,"total_mv") is not None else (price*shares if price and shares else None),"tushare_api"),"current_pe":_fact(current_pe,"tushare_api"),"current_pb":_fact(current_pb,"tushare_api"),"quality_flag":"distorted" if current_pe is not None and current_pe<=0 else "normal"},"earnings_history":annuals,"balance_sheet":{"cash_and_cash_equivalents":_fact(cash,"tushare_api"),"interest_bearing_debt":_fact(debt,"derived",formula="st_borr_plus_lt_borr_plus_bonds_pay_plus_current_debt",inputs=["st_borr","lt_borr","bonds_pay","non_cur_liab_due_1y"]),"net_cash":_fact(cash - debt if cash is not None and debt is not None else None,"derived",formula="cash_minus_interest_bearing_debt",inputs=["cash_and_cash_equivalents","interest_bearing_debt"]),"shareholders_equity":_fact(_number(balance_latest,"total_hldr_eqy_exc_min_int"),"tushare_api"),"shares_outstanding":_fact(shares,"tushare_api")},"dividend":{"history":snapshot.get("dividend_history",[]),"coverage_by_year":[{"period":x["period"],"payout_ratio":x["payout_ratio"],"dividend_to_fcf_ratio":x["dividend_to_fcf_ratio"]} for x in annuals]},"valuation_history":{"pe":percentile(daily.get("pe_ttm",[]),current_pe),"pb":percentile(daily.get("pb",[]),current_pb)},"missing_data":missing}
        if dry_run:
            return {"symbol": symbol.upper(), "version": actual, "path": str(output), "dry_run": True}
        write_json_atomic(output,input_data); return {"symbol":symbol.upper(),"version":actual,"path":str(output)}

    def _folder(self,symbol:str,version:str|None)->tuple[Path,str]:
        analysis=self.research_root/symbol.upper()/"analysis"
        choices=[p for p in analysis.iterdir() if p.is_dir() and (p/"input_snapshot.json").exists()]
        target=analysis/version if version else sorted(choices,key=lambda p:p.name)[-1]
        if not target.exists(): raise AnalystInputError("analysis version missing")
        return target,target.name

def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser();parser.add_argument("--symbol",required=True);parser.add_argument("--version");parser.add_argument("--force",action="store_true");parser.add_argument("--dry-run",action="store_true");parser.add_argument("--research-root",type=Path,default=DEFAULT_RESEARCH_ROOT);args=parser.parse_args(argv)
    try: print(ValuationInputBuilder(research_root=args.research_root).build(args.symbol,version=args.version,force=args.force,dry_run=args.dry_run));return 0
    except (AnalystInputError,ValuationDataError,FileExistsError,ValueError) as exc: print({"status":"failed","error":str(exc)});return 1
if __name__=="__main__": raise SystemExit(main())
