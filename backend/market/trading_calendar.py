from __future__ import annotations

from datetime import date
from typing import Any

from backend.data_sources.tushare import TushareClient


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    if isinstance(frame, list):
        return frame
    raise ValueError("Tushare trade calendar response is not tabular")


def is_a_share_trading_day(client: TushareClient, trade_date: date) -> bool:
    """Return SSE open status for one exact date; missing data fails closed with an error."""
    requested = trade_date.strftime("%Y%m%d")
    frame = client.call(
        "trade_cal", exchange="SSE", start_date=requested, end_date=requested,
        fields="cal_date,is_open",
    )
    for item in _records(frame):
        if str(item.get("cal_date", "")).replace("-", "") != requested:
            continue
        value = item.get("is_open")
        if str(value).strip() in {"1", "1.0", "True", "true"}:
            return True
        if str(value).strip() in {"0", "0.0", "False", "false"}:
            return False
        raise ValueError(f"invalid Tushare is_open value for {trade_date.isoformat()}: {value!r}")
    raise ValueError(f"Tushare trade calendar has no record for {trade_date.isoformat()}")
