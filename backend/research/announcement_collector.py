from __future__ import annotations

from .source_metadata import SourceDocument


def classify_announcement(title: str) -> str | None:
    clean = title.replace(" ", "")
    if "业绩预告" in clean: return "earnings_forecast"
    if "业绩快报" in clean: return "earnings_flash"
    if "利润分配" in clean or "权益分派" in clean or "分红" in clean: return "dividend"
    if "回购" in clean: return "share_repurchase"
    if "重大资产重组" in clean or "重大资产出售" in clean or "重大资产购买" in clean: return "major_asset_event"
    if any(word in clean for word in ("董事长变更", "总经理变更", "董事会秘书变更", "聘任总经理", "总经理辞职")): return "management_change"
    return None
