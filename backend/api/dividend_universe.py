"""API for maintaining the formal stable-dividend universe."""
from __future__ import annotations

import re
import csv
import json
import sqlite3
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient
from backend.dividend.dividend_candidate_rules import classify_industry, target_years
from backend.dividend.dividend_candidate_service import (
    DividendCandidateService,
    TushareDividendProvider,
    _aggregate_events,
    _unique_valid_events,
)
from backend.dividend.annual_dps import METHOD
from backend.dividend.run_high_dividend_watch_full_dryrun import run as run_high_dividend_dryrun
from backend.dividend.universe_repository import DividendUniverseRepository
from backend.expectation_gap.database import PROJECT_ROOT, connect, connect_readonly, migrate


router = APIRouter(prefix="/api/dividend/universe", tags=["dividend"])
_runs: dict[str, dict[str, object]] = {}
_run_lock = threading.Lock()
SCAN_OUTPUT = PROJECT_ROOT / "exports" / "dividend" / "high_dividend_watch_full_dryrun.csv"
SCAN_SUMMARY = SCAN_OUTPUT.with_suffix(".summary.json")
ALLOWED_SCAN_SUBTYPES = {"stable_monopoly", "resource_monopoly_cyclical", "high_dividend_watch"}


class ValidateRequest(BaseModel):
    symbol: str
    calculation_date: date | None = None


class AddRequest(ValidateRequest):
    stability_subtype: str
    monopoly_type: str
    manual_reason: str
    acknowledge_warnings: bool = False


class StatusRequest(BaseModel):
    is_enabled: bool


class RescanRequest(BaseModel):
    calculation_date: date | None = None


class CandidateAddRequest(BaseModel):
    confirm: bool = False


def _number(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _load_scan_result() -> dict[str, object]:
    if not SCAN_OUTPUT.exists() or not SCAN_SUMMARY.exists():
        return {"status": "never_run", "summary": {}, "items": []}
    payload = json.loads(SCAN_SUMMARY.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary") or {})
    items = []
    with SCAN_OUTPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            item: dict[str, object] = dict(raw)
            for year in (2023, 2024, 2025):
                item[f"{year}_dps"] = _number(raw.get(f"{year}_dps"))
                item[f"{year}_current_basis_dps"] = _number(raw.get(f"{year}_current_basis_dps"))
                item[f"{year}_historical_yield"] = _number(raw.get(f"{year}_historical_yield"))
                item[f"{year}_reference_price"] = _number(raw.get(f"{year}_reference_price"))
                count = raw.get(f"{year}_event_count")
                item[f"{year}_event_count"] = int(count) if count not in (None, "") else None
            for key in (
                "three_year_historical_average_yield", "three_year_average_dps",
                "latest_price", "latest_year_yield", "three_year_average_yield",
                "conservative_three_year_current_yield", "dividend_variation_ratio",
            ):
                item[key] = _number(raw.get(key))
            items.append(item)
    connection = connect_readonly()
    try:
        existing = {row[0] for row in connection.execute(
            "SELECT symbol FROM dividend_stable_universe WHERE market='CN'"
        )}
    finally:
        connection.close()
    for item in items:
        item["already_in_universe"] = item["symbol"] in existing
    summary["qualified_count"] = len(items)
    summary["already_in_universe_count"] = sum(bool(item["already_in_universe"]) for item in items)
    summary["new_candidate_count"] = len(items) - int(summary["already_in_universe_count"])
    return {"status": "completed", "summary": summary, "items": items}


def _symbol(value: str) -> str:
    value = value.strip().upper()
    if len(value) == 6 and value.isdigit():
        value += ".SH" if value.startswith(("5", "6", "9")) else ".SZ"
    if not re.fullmatch(r"\d{6}\.(SH|SZ)", value):
        raise HTTPException(422, "证券代码格式无效")
    if value.startswith(("20", "900")):
        raise HTTPException(422, "仅支持普通A股，不支持B股")
    return value


def _run(callback):
    connection = connect()
    migrate(connection)
    try:
        return callback(connection, DividendUniverseRepository(connection))
    except HTTPException:
        raise
    except (ValueError, sqlite3.Error) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        connection.close()


@router.get("")
def listing(
    include_disabled: bool = False,
    search: str = "",
    stability_subtype: str = "",
    monopoly_type: str = "",
):
    def action(_connection, repository):
        years, items = repository.list(
            include_disabled=include_disabled,
            search=search,
            subtype=stability_subtype,
            monopoly_type=monopoly_type,
        )
        return {
            "target_years": years,
            "total": len(items),
            "enabled_count": sum(item["is_enabled"] for item in items),
            "disabled_count": sum(not item["is_enabled"] for item in items),
            "items": items,
        }

    return _run(action)


@router.get("/search")
def search(q: str = Query(..., min_length=1)):
    return _run(lambda _connection, repository: {"items": repository.search_securities(q)})


def _validate(connection, repository, symbol: str, calculation_date: date) -> dict[str, object]:
    row = repository.security(symbol)
    if row is None:
        raise HTTPException(404, "证券不存在于主数据")
    if not row[3] or (row[4] and row[4] <= calculation_date.isoformat()):
        return {"symbol": symbol, "company_name": row[1], "can_add": False, "warnings": ["证券已退市或非正常上市"]}

    provider = TushareDividendProvider(TushareClient(DataSourceSettings.from_env().tushare_token))
    events = provider.fetch_events([symbol])
    years = target_years(calculation_date)
    totals, _ = _aggregate_events(events, years)
    values = [totals[symbol].get(year, 0.0) for year in years]
    counts = {year: len(_unique_valid_events(events, symbol, (year,))) for year in years}
    annual_dps = dict(zip(map(str, years), values))
    if any(value <= 0 for value in values):
        return {"symbol": symbol, "company_name": row[1], "target_years": years, "annual_dps": annual_dps, "can_add": False, "warnings": ["三个目标年度DPS无法完整确认"]}

    average = sum(values) / 3
    ratio = values[-1] / average
    monopoly_type = classify_industry(row[6], row[7], row[8], row[1])
    warnings: list[str] = []
    if row[5]:
        warnings.append("ST证券，需人工确认")
    if monopoly_type is None:
        warnings.append("不符合自动稳定行业规则，需人工确认")
    if ratio < 0.7:
        warnings.append("最近一年DPS低于三年平均70%，需人工确认")
    return {
        "symbol": symbol,
        "company_name": row[1],
        "target_years": years,
        "annual_dps": annual_dps,
        "dividend_event_counts": {str(year): counts[year] for year in years},
        "three_year_total_dps": sum(values),
        "three_year_average_dps": average,
        "latest_to_average_ratio": ratio,
        "listing_status": "active",
        "is_st": bool(row[5]),
        "automatic_rule_result": "included" if monopoly_type else "excluded",
        "automatic_rule_reason": None if monopoly_type else "行业不属于自动稳定行业",
        "suggested_stability_subtype": "stable_monopoly" if monopoly_type else "resource_monopoly_cyclical",
        "suggested_monopoly_type": monopoly_type or "manual_review_required",
        "warnings": warnings,
        "can_add": True,
    }


@router.post("/validate")
def validate(payload: ValidateRequest):
    return _run(lambda connection, repository: _validate(connection, repository, _symbol(payload.symbol), payload.calculation_date or date.today()))


@router.post("")
def add(payload: AddRequest):
    if not payload.acknowledge_warnings:
        raise HTTPException(422, "请确认已了解风险提示")

    def action(connection, repository):
        symbol = _symbol(payload.symbol)
        result = _validate(connection, repository, symbol, payload.calculation_date or date.today())
        if not result["can_add"]:
            raise HTTPException(422, "该证券无法完成分红数据验证")
        existing = connection.execute("SELECT is_enabled FROM dividend_stable_universe WHERE market='CN' AND symbol=?", (symbol,)).fetchone()
        if existing:
            return {"status": "already_exists" if existing[0] else "disabled_exists", "symbol": symbol, "validation": result}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row = repository.security(symbol)
        with connection:
            connection.execute(
                """INSERT INTO dividend_stable_universe(market,symbol,company_name,industry_level_1,industry_level_2,monopoly_type,stability_subtype,inclusion_source,inclusion_reason,risk_note,included_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CN", symbol, row[1], row[6], row[7], payload.monopoly_type, payload.stability_subtype, "manual_review", payload.manual_reason, ";".join(result["warnings"]), now, now),
            )
            for year, value in result["annual_dps"].items():
                connection.execute(
                    """INSERT INTO annual_cash_dividend_summaries(market,symbol,calendar_year,cash_dividend_per_share,dividend_event_count,calculation_method,source,data_quality_status,calculated_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    ("CN", symbol, int(year), value, result["dividend_event_counts"][year], METHOD, "tushare", "complete", now, now),
                )
        return {"status": "added", "symbol": symbol, "validation": result}

    return _run(action)


@router.patch("/{symbol}/status")
def status(symbol: str, payload: StatusRequest):
    def action(connection, _repository):
        symbol_value = _symbol(symbol)
        existing = connection.execute("SELECT is_enabled FROM dividend_stable_universe WHERE market='CN' AND symbol=?", (symbol_value,)).fetchone()
        if not existing:
            raise HTTPException(404, "股票池中不存在该证券")
        if payload.is_enabled:
            years = [row[0] for row in connection.execute("SELECT DISTINCT calendar_year FROM annual_cash_dividend_summaries WHERE market='CN' AND symbol=? ORDER BY calendar_year DESC LIMIT 3", (symbol_value,))][::-1]
            count = connection.execute("SELECT COUNT(*) FROM annual_cash_dividend_summaries WHERE market='CN' AND symbol=? AND calendar_year IN (?,?,?)", (symbol_value, *years)).fetchone()[0] if len(years) == 3 else 0
            if count != 3:
                raise HTTPException(422, "缺少最近三个完整年度DPS，不能重新启用")
        with connection:
            connection.execute("UPDATE dividend_stable_universe SET is_enabled=?,updated_at=? WHERE market='CN' AND symbol=?", (int(payload.is_enabled), datetime.now(timezone.utc).isoformat(timespec="seconds"), symbol_value))
        return {"symbol": symbol_value, "is_enabled": payload.is_enabled}

    return _run(action)


def _scan_worker(run_id: str, calculation_date: date) -> None:
    try:
        run_high_dividend_dryrun(SCAN_OUTPUT, calculation_date)
        result = _load_scan_result()
        with _run_lock:
            _runs[run_id].update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "summary": result["summary"], "items": result["items"],
            })
    except Exception as exc:  # surfaced to the UI; no database mutation occurs
        with _run_lock:
            _runs[run_id].update({"status": "failed", "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "error": f"{type(exc).__name__}: {exc}"})


@router.post("/rescan")
def rescan(payload: RescanRequest):
    with _run_lock:
        if any(run["status"] == "running" for run in _runs.values()):
            raise HTTPException(409, "已有分红池扫描正在运行")
        run_id = uuid.uuid4().hex
        calculation_date = payload.calculation_date or date.today()
        _runs[run_id] = {"run_id": run_id, "status": "running", "calculation_date": calculation_date.isoformat(), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    threading.Thread(target=_scan_worker, args=(run_id, calculation_date), daemon=True).start()
    return _runs[run_id]


@router.get("/rescan/latest")
def latest_rescan():
    return _load_scan_result()


@router.post("/rescan/candidates/{symbol}/add")
def add_scanned_candidate(symbol: str, payload: CandidateAddRequest):
    if not payload.confirm:
        raise HTTPException(422, "必须确认后才能加入正式股票池")
    symbol_value = _symbol(symbol)
    result = _load_scan_result()
    candidate = next((item for item in result["items"] if item["symbol"] == symbol_value), None)
    if candidate is None:
        raise HTTPException(404, "上一次成功扫描中没有该候选")
    subtype = str(candidate["suggested_stability_subtype"])
    if subtype not in ALLOWED_SCAN_SUBTYPES:
        raise HTTPException(422, "候选建议类型无效")
    counts = {year: candidate.get(f"{year}_event_count") for year in (2023, 2024, 2025)}
    if any(not isinstance(value, int) or value <= 0 for value in counts.values()):
        raise HTTPException(422, "候选缺少DPS事件计数，请重新筛选候选池后再加入")

    connection = connect()
    try:
        migrate(connection)
        existing = connection.execute(
            "SELECT 1 FROM dividend_stable_universe WHERE market='CN' AND symbol=?", (symbol_value,)
        ).fetchone()
        if existing:
            return {"status": "already_exists", "symbol": symbol_value}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        monopoly_type = {
            "stable_monopoly": "high_dividend_scan_stable",
            "resource_monopoly_cyclical": "high_dividend_scan_resource",
            "high_dividend_watch": "high_dividend_watch",
        }[subtype]
        with connection:
            connection.execute(
                """INSERT INTO dividend_stable_universe(
                       market,symbol,company_name,industry_level_1,industry_level_2,
                       monopoly_type,stability_subtype,inclusion_source,inclusion_reason,
                       risk_note,is_enabled,included_at,updated_at
                   ) VALUES('CN',?,?,?,?,?,?,?,'高股息观察池候选人工确认','',1,?,?)""",
                (
                    symbol_value, candidate["company_name"], candidate.get("industry_level_1"),
                    candidate.get("industry_level_2"), monopoly_type, subtype, "manual_review", now, now,
                ),
            )
            annual_columns = {row[1] for row in connection.execute("PRAGMA table_info(annual_cash_dividend_summaries)")}
            for year in (2023, 2024, 2025):
                if "current_basis_dps" in annual_columns:
                    connection.execute("""INSERT INTO annual_cash_dividend_summaries(market,symbol,calendar_year,cash_dividend_per_share,dividend_event_count,calculation_method,source,data_quality_status,calculated_at,updated_at,current_basis_dps,share_basis_as_of) VALUES('CN',?,?,?,?,?,'tushare','complete',?,?,?,?)""", (symbol_value, year, candidate[f"{year}_dps"], counts[year], METHOD, now, now, candidate.get(f"{year}_current_basis_dps"), candidate.get("share_basis_as_of")))
                else:
                    connection.execute("""INSERT INTO annual_cash_dividend_summaries(market,symbol,calendar_year,cash_dividend_per_share,dividend_event_count,calculation_method,source,data_quality_status,calculated_at,updated_at) VALUES('CN',?,?,?,?,?,'tushare','complete',?,?)""", (symbol_value, year, candidate[f"{year}_dps"], counts[year], METHOD, now, now))
        return {"status": "added", "symbol": symbol_value, "stability_subtype": subtype}
    finally:
        connection.close()


@router.get("/rescan/{run_id}")
def rescan_status(run_id: str):
    with _run_lock:
        run = _runs.get(run_id)
        if run is None:
            raise HTTPException(404, "未找到该扫描任务")
        return run
