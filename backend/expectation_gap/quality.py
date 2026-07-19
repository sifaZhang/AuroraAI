from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

SEVERITY = {"ok": 0, "warning": 1, "suspicious": 2, "excluded": 3}
IMPACTFUL_ACTIONS = {"split", "consolidation", "rights_issue"}


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _merge_status(*statuses: str) -> str:
    return max(statuses, key=lambda item: SEVERITY[item])


def _normalize_actions(actions: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for action in actions:
        if isinstance(action, date):
            normalized.append({"effective_date": action, "corporate_action_type": "split", "ratio": None})
        else:
            item = dict(action)
            item["effective_date"] = _date(item.get("effective_date"))
            normalized.append(item)
    return normalized


def _ratio_multiplier(action: dict[str, Any]) -> float | None:
    if action.get("corporate_action_type") not in {"split", "consolidation"}:
        return None
    try:
        base, entitlement = str(action.get("ratio") or "").split(":", 1)
        base_value, entitlement_value = float(base), float(entitlement)
        return base_value / entitlement_value if base_value > 0 and entitlement_value > 0 else None
    except (TypeError, ValueError):
        return None


def evaluate_quality(row: dict[str, Any], actions: list[Any], *, today: date | None = None,
                     overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    actions = _normalize_actions(actions)
    overrides = overrides or {}
    price_date = _date(row.get("price_time"))
    price_status, price_reasons, price_rankable = "ok", [], True
    if not _positive(row.get("last_price")):
        price_status, price_reasons, price_rankable = "excluded", ["invalid_price"], False
    elif price_date is None or today - price_date > timedelta(days=30):
        price_status, price_reasons, price_rankable = "excluded", ["stale_price"], False
    elif today - price_date > timedelta(days=7):
        price_status, price_reasons = "warning", ["stale_price"]
    low_price = row.get("market", "HK") == "HK" and _positive(row.get("last_price")) and float(row["last_price"]) < 1

    def target_quality(prefix: str) -> tuple[str, list[str], bool, dict[str, Any]]:
        target_field = "morningstar_fair_value" if prefix == "morningstar" else "analyst_average_target"
        target, gap = row.get(target_field), row.get(f"{prefix}_gap_pct")
        target_date = _date(row.get(f"{prefix}_data_date"))
        details: dict[str, Any] = {}
        if not _positive(target):
            reasons = [*price_reasons, "invalid_target"]
            if low_price:
                reasons.append("low_price")
            return _merge_status(price_status, "excluded"), reasons, False, details
        status, reasons, rankable = price_status, list(price_reasons), price_rankable
        if low_price:
            reasons.append("low_price")
        if gap is not None and float(gap) > 1000:
            status, rankable = _merge_status(status, "suspicious"), False
            reasons.append("extreme_gap")
        elif gap is not None and float(gap) >= 500:
            status, rankable = _merge_status(status, "suspicious"), False
            reasons.append("extreme_gap")
        elif gap is not None and float(gap) > 200:
            status = _merge_status(status, "warning")
            reasons.append("extreme_gap")
        if low_price and gap is not None and float(gap) > 200:
            status = _merge_status(status, "warning")
            reasons.append("low_price_extreme_gap")
        if target_date and today - target_date > timedelta(days=365):
            status = _merge_status(status, "warning")
            reasons.append("stale_target")
        impactful = [a for a in actions if a.get("corporate_action_type") in IMPACTFUL_ACTIONS and a.get("effective_date")]
        if target_date and any(a["effective_date"] > target_date for a in impactful):
            status, rankable = _merge_status(status, "suspicious"), False
            reasons.append("possible_corporate_action")
        nearby = [a for a in impactful if target_date and abs((a["effective_date"] - target_date).days) <= 30]
        if nearby:
            status = _merge_status(status, "warning")
            reasons.append("corporate_action_review")
            details["nearby_corporate_actions"] = [{"type": a.get("corporate_action_type"),
                "effective_date": a["effective_date"].isoformat(), "ratio": a.get("ratio"),
                "days_from_target": (a["effective_date"] - target_date).days} for a in nearby]
        if gap is not None and float(gap) > 500 and _positive(row.get("last_price")):
            trials = []
            for action in impactful:
                multiplier = _ratio_multiplier(action)
                if multiplier is None:
                    continue
                trial_target = float(target) * multiplier
                trial_gap = (trial_target / float(row["last_price"]) - 1) * 100
                trials.append({"action_type": action.get("corporate_action_type"), "effective_date": action["effective_date"].isoformat(),
                    "ratio": action.get("ratio"), "trial_target": trial_target, "original_gap_pct": float(gap), "trial_gap_pct": trial_gap})
            plausible = [trial for trial in trials if -80 <= trial["trial_gap_pct"] <= 200]
            if trials:
                details["corporate_action_adjustment_trials"] = trials
            if plausible:
                status, rankable = _merge_status(status, "suspicious"), False
                reasons.append("possible_unadjusted_target")
                details["possible_unadjusted_target"] = plausible
        if prefix == "analyst":
            count = row.get("analyst_count")
            if count is None or int(count) < 3:
                status = _merge_status(status, "warning")
                reasons.append("low_analyst_coverage")
                rankable = False
            if count is not None and int(count) == 1 and gap is not None and float(gap) > 200:
                status, rankable = "excluded", False
                reasons.append("single_analyst_extreme_gap")
        override = overrides.get(prefix)
        if override:
            reasons.append(override["reason"])
            details["manual_override"] = override
            if override["action"] == "exclude":
                status, rankable = "excluded", False
            elif override["action"] == "warning":
                status, rankable = _merge_status(status, "warning"), True
            elif override["action"] == "allow" and price_rankable:
                status, rankable = "ok", True
        return status, list(dict.fromkeys(reasons)), rankable, details

    ms_status, ms_reasons, ms_rankable, ms_details = target_quality("morningstar")
    an_status, an_reasons, an_rankable, an_details = target_quality("analyst")
    valid_statuses, combined_reasons = [], list(price_reasons)
    if _positive(row.get("morningstar_fair_value")):
        valid_statuses.append(ms_status); combined_reasons.extend(r for r in ms_reasons if r != "invalid_target")
    if _positive(row.get("analyst_average_target")):
        valid_statuses.append(an_status); combined_reasons.extend(r for r in an_reasons if r != "invalid_target")
    if low_price:
        combined_reasons.append("low_price")
    combined_status = price_status if not price_rankable else (_merge_status(*valid_statuses) if valid_statuses else "excluded")
    if not valid_statuses:
        combined_reasons.append("invalid_target")
    return {"quality_status": combined_status, "quality_reasons": list(dict.fromkeys(combined_reasons)),
        "is_rankable": ms_rankable or an_rankable, "morningstar_quality_status": ms_status,
        "morningstar_quality_reasons": ms_reasons, "morningstar_is_rankable": ms_rankable,
        "morningstar_quality_details": ms_details, "analyst_quality_status": an_status,
        "analyst_quality_reasons": an_reasons, "analyst_is_rankable": an_rankable,
        "analyst_quality_details": an_details}


def refresh_quality(connection, *, today: date | None = None) -> int:
    rows = connection.execute("SELECT s.id AS stock_id,s.market,e.* FROM stocks s JOIN stock_expectations e ON e.stock_id=s.id WHERE s.is_active=1").fetchall()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for raw in rows:
        row = dict(raw)
        actions = [dict(action) for action in connection.execute(
            "SELECT corporate_action_type,effective_date,ratio FROM corporate_actions WHERE stock_id=?", (row["stock_id"],))]
        overrides = {item["source"]: dict(item) for item in connection.execute(
            "SELECT source,action,reason,note,reviewed_at FROM expectation_quality_overrides WHERE stock_id=?", (row["stock_id"],))}
        result = evaluate_quality(row, actions, today=today, overrides=overrides)
        connection.execute("""INSERT INTO stock_expectation_quality(stock_id,quality_status,quality_reasons,is_rankable,
            morningstar_quality_status,morningstar_quality_reasons,morningstar_is_rankable,morningstar_quality_details,
            analyst_quality_status,analyst_quality_reasons,analyst_is_rankable,analyst_quality_details,calculated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stock_id) DO UPDATE SET quality_status=excluded.quality_status,
            quality_reasons=excluded.quality_reasons,is_rankable=excluded.is_rankable,morningstar_quality_status=excluded.morningstar_quality_status,
            morningstar_quality_reasons=excluded.morningstar_quality_reasons,morningstar_is_rankable=excluded.morningstar_is_rankable,
            morningstar_quality_details=excluded.morningstar_quality_details,analyst_quality_status=excluded.analyst_quality_status,
            analyst_quality_reasons=excluded.analyst_quality_reasons,analyst_is_rankable=excluded.analyst_is_rankable,
            analyst_quality_details=excluded.analyst_quality_details,calculated_at=excluded.calculated_at""", (
            row["stock_id"], result["quality_status"], json.dumps(result["quality_reasons"], ensure_ascii=False), int(result["is_rankable"]),
            result["morningstar_quality_status"], json.dumps(result["morningstar_quality_reasons"], ensure_ascii=False), int(result["morningstar_is_rankable"]), json.dumps(result["morningstar_quality_details"], ensure_ascii=False),
            result["analyst_quality_status"], json.dumps(result["analyst_quality_reasons"], ensure_ascii=False), int(result["analyst_is_rankable"]), json.dumps(result["analyst_quality_details"], ensure_ascii=False), now))
    return len(rows)
