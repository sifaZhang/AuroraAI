from __future__ import annotations

import re
from datetime import date
from typing import Any

from .source_metadata import SourceDocument


_YEAR = re.compile(r"(20\d{2})")


def classify_financial_report(title: str, symbol: str, source_url: str, published_at: str | None, announcement_id: str | None) -> SourceDocument | None:
    clean = title.replace(" ", "")
    if any(value in clean for value in ("摘要", "业绩预告", "业绩快报", "修订说明", "英文版")):
        return None
    year = _YEAR.search(clean)
    if not year:
        return None
    if "半年度报告" in clean:
        kind, period = "half_year_report", f"{year.group(1)}H1"
    elif "第一季度报告" in clean or "一季度报告" in clean:
        kind, period = "q1_report", f"{year.group(1)}Q1"
    elif "第三季度报告" in clean or "三季度报告" in clean:
        kind, period = "q3_report", f"{year.group(1)}Q3"
    elif "年度报告" in clean:
        kind, period = "annual_report", year.group(1)
    else:
        return None
    return SourceDocument(symbol, title, source_url, published_at, announcement_id, kind, period=period)


def choose_initial_reports(documents: list[SourceDocument]) -> list[SourceDocument]:
    """Latest annual plus the newest available current-year periodic report."""
    annual = sorted((x for x in documents if x.document_type == "annual_report"), key=lambda x: (x.period or "", x.published_at or ""), reverse=True)
    period_rank = {"Q1": 1, "H1": 2, "Q3": 3}
    def periodic_key(item: SourceDocument) -> tuple[int, int, str]:
        period = item.period or ""
        return (int(period[:4]) if period[:4].isdigit() else 0, period_rank.get(period[4:], 0), item.published_at or "")
    periodic = sorted((x for x in documents if x.document_type != "annual_report"), key=periodic_key, reverse=True)
    completed_year = str(date.today().year - 1)
    completed_annual = [item for item in annual if (item.period or "") <= completed_year]
    return [*(completed_annual or annual)[:1], *periodic[:1]]
