"""Per-run in-memory Tushare stk_limit cache; no database persistence."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from backend.data_sources.symbol_normalizer import normalize_symbol

@dataclass(frozen=True)
class PriceLimit:
 symbol:str; trade_date:date; pre_close:Decimal|None; upper_limit:Decimal|None; lower_limit:Decimal|None; source:str="tushare_stk_limit"

def load_price_limits(client, trade_dates):
    values={}; failures={}
    for day in trade_dates:
        try:
            raw=client.call("stk_limit",trade_date=day.strftime("%Y%m%d"),fields="ts_code,trade_date,pre_close,up_limit,down_limit")
            rows=raw.to_dict("records") if hasattr(raw,"to_dict") else raw
            for row in rows or ():
                try:
                    symbol=normalize_symbol(row.get("ts_code")); reported=date.fromisoformat(str(row.get("trade_date")).replace("/","-")[:10]) if "-" in str(row.get("trade_date")) else day
                    if reported==day: values[(symbol,day)]=PriceLimit(symbol,day,*[Decimal(str(row.get(k))) if row.get(k) is not None else None for k in ("pre_close","up_limit","down_limit")])
                except (ValueError,TypeError): continue
        except Exception as exc: failures[day]=type(exc).__name__
    return values,failures
