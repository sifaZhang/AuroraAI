from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.expectation_gap.database import PROJECT_ROOT, connect, migrate
from backend.expectation_gap.quality import refresh_quality

FIELDS = [
    "futu_code", "name", "last_price", "price_time", "morningstar_fair_value",
    "morningstar_gap_pct", "morningstar_star_rating", "morningstar_rating_type",
    "morningstar_data_date", "analyst_average_target", "analyst_gap_pct", "analyst_count",
    "analyst_data_date", "listing_date", "data_source",
]
FOCUS_CODES = ["HK.02661", "HK.02525", "HK.02469", "HK.02565", "HK.06628", "HK.02656", "HK.01280"]


def audit_rows(connection) -> list[dict[str, Any]]:
    rows = connection.execute("""SELECT s.id AS stock_id,s.futu_code,s.name,s.listing_date,e.*
        FROM stocks s JOIN stock_expectations e ON e.stock_id=s.id WHERE s.is_active=1""").fetchall()
    result = []
    for raw in rows:
        item = dict(raw)
        item["data_source"] = ";".join(filter(None, [
            f"price:{item.get('price_source')}" if item.get("price_source") else None,
            f"morningstar:{item.get('morningstar_source')}" if item.get("morningstar_source") else None,
            f"analyst:{item.get('analyst_source')}" if item.get("analyst_source") else None,
        ]))
        result.append(item)
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] = FIELDS) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _action_records(code: str, rehab_frame, checked_at: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for _, row in rehab_frame.iterrows():
        event_date = str(row.get("ex_div_date") or "")[:10] or None
        candidates = [
            ("split", row.get("split_base"), row.get("split_ert")),
            ("consolidation", row.get("join_base"), row.get("join_ert")),
            ("rights_issue", row.get("allot_base"), row.get("allot_ert")),
            ("other", row.get("bonus_base"), row.get("bonus_ert")),
            ("other", row.get("transfer_base"), row.get("transfer_ert")),
            ("other", row.get("spin_off_base"), row.get("spin_off_ert")),
        ]
        for action_type, base, entitlement in candidates:
            if _number(base) > 0 and _number(entitlement) > 0:
                actions.append({"corporate_action_type": action_type, "effective_date": event_date,
                                "ratio": f"{base}:{entitlement}", "source": "futu_opend",
                                "raw_summary": json.dumps({key: row.get(key) for key in row.index
                                    if row.get(key) not in (None, "", 0, 0.0)}, ensure_ascii=False, default=str),
                                "checked_at": checked_at})
    return actions


def fetch_corporate_actions(connection, stocks: list[dict[str, Any]]) -> dict[str, Any]:
    from futu import OpenQuoteContext, RET_OK
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors: dict[str, str] = {}
    counts = {"queried": len(stocks), "actions": 0, "errors": 0}
    context = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        code_changes: dict[str, list[dict[str, Any]]] = {}
        for start in range(0, len(stocks), 50):
            codes = [item["futu_code"] for item in stocks[start:start + 50]]
            ret, frame = context.get_code_change(code_list=codes)
            if ret == RET_OK and frame is not None:
                for _, row in frame.iterrows():
                    code_changes.setdefault(str(row.get("code")), []).append(row.to_dict())
            elif ret != RET_OK:
                errors[f"code_change:{','.join(codes)}"] = str(frame)
        for stock in stocks:
            code, stock_id = stock["futu_code"], stock["stock_id"]
            actions = []
            try:
                ret, frame = context.get_rehab(code)
                if ret == RET_OK and frame is not None:
                    actions.extend(_action_records(code, frame, checked_at))
                else:
                    errors[f"rehab:{code}"] = str(frame)
            except Exception as exc:
                errors[f"rehab:{code}"] = str(exc)
            for change in code_changes.get(code, []):
                actions.append({"corporate_action_type": "code_change",
                    "effective_date": str(change.get("effective_time") or "")[:10] or None,
                    "ratio": None, "source": "futu_opend",
                    "raw_summary": json.dumps(change, ensure_ascii=False, default=str), "checked_at": checked_at})
            target_dates = [value for value in (stock.get("morningstar_data_date"), stock.get("analyst_data_date")) if value]
            for action in actions:
                possible = (action["corporate_action_type"] in {"split", "consolidation", "rights_issue"}
                            and bool(target_dates) and bool(action["effective_date"])
                            and any(action["effective_date"] > str(target)[:10] for target in target_dates))
                connection.execute("""INSERT INTO corporate_actions(stock_id,corporate_action_type,effective_date,ratio,
                    possible_price_mismatch,source,raw_summary,checked_at) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(stock_id,corporate_action_type,effective_date,ratio) DO UPDATE SET
                    possible_price_mismatch=excluded.possible_price_mismatch,source=excluded.source,
                    raw_summary=excluded.raw_summary,checked_at=excluded.checked_at""",
                    (stock_id, action["corporate_action_type"], action["effective_date"], action["ratio"],
                     int(possible), action["source"], action["raw_summary"], action["checked_at"]))
                counts["actions"] += 1
    finally:
        context.close()
    counts["errors"] = len(errors)
    return {**counts, "error_details": errors}


def run(output_dir: Path, *, skip_corporate_actions: bool = False) -> dict[str, Any]:
    connection = connect()
    migrate(connection)
    rows = audit_rows(connection)
    over200 = [row for row in rows if max(_number(row.get("morningstar_gap_pct")), _number(row.get("analyst_gap_pct"))) > 200]
    corporate_summary = {"queried": 0, "actions": 0, "errors": 0, "mode": "live"}
    if not skip_corporate_actions:
        corporate_summary = fetch_corporate_actions(connection, over200)
        corporate_summary["mode"] = "live"
    else:
        corporate_summary = {"queried": 0, "actions": connection.execute(
            "SELECT COUNT(*) FROM corporate_actions").fetchone()[0], "errors": 0, "mode": "cached"}
    refresh_quality(connection)
    connection.commit()
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = {
        "morningstar_top100.csv": sorted([r for r in rows if r.get("morningstar_gap_pct") is not None], key=lambda r: _number(r["morningstar_gap_pct"]), reverse=True)[:100],
        "analyst_top100.csv": sorted([r for r in rows if r.get("analyst_gap_pct") is not None], key=lambda r: _number(r["analyst_gap_pct"]), reverse=True)[:100],
        "gap_over_200_pct.csv": sorted(over200, key=lambda r: max(_number(r.get("morningstar_gap_pct")), _number(r.get("analyst_gap_pct"))), reverse=True),
        "price_below_1_hkd.csv": [r for r in rows if r["futu_code"].startswith("HK.") and 0 < _number(r.get("last_price")) < 1],
        "analyst_count_below_3.csv": [r for r in rows if _number(r.get("analyst_average_target")) > 0 and (r.get("analyst_count") is None or int(r["analyst_count"]) < 3)],
    }
    for filename, report_rows in reports.items():
        _write_csv(output_dir / filename, report_rows)
    action_fields = ["futu_code", "name", "corporate_action_type", "effective_date", "ratio", "possible_price_mismatch", "source", "raw_summary", "checked_at"]
    actions = [dict(row) for row in connection.execute("""SELECT s.futu_code,s.name,a.corporate_action_type,a.effective_date,
        a.ratio,a.possible_price_mismatch,a.source,a.raw_summary,a.checked_at FROM corporate_actions a
        JOIN stocks s ON s.id=a.stock_id WHERE s.futu_code IN ({}) ORDER BY s.futu_code,a.effective_date""".format(
            ",".join("?" for _ in over200)), [r["futu_code"] for r in over200]).fetchall()] if over200 else []
    _write_csv(output_dir / "corporate_actions_over_200_pct.csv", actions, action_fields)
    focus_fields = list(dict.fromkeys([*FIELDS, "analyst_high_target", "analyst_low_target", "price_source",
        "morningstar_source", "analyst_source", "morningstar_status", "analyst_status", "last_error"]))
    focus = [row for row in rows if row["futu_code"] in FOCUS_CODES]
    _write_csv(output_dir / "focus_stocks_raw.csv", focus, focus_fields)
    quality_distribution = {row[0]: row[1] for row in connection.execute(
        "SELECT quality_status,COUNT(*) FROM stock_expectation_quality GROUP BY quality_status")}
    focus_quality = [dict(row) for row in connection.execute("""SELECT s.futu_code,q.quality_status,q.quality_reasons,
        q.morningstar_quality_status,q.morningstar_quality_reasons,q.morningstar_is_rankable,
        q.analyst_quality_status,q.analyst_quality_reasons,q.analyst_is_rankable,q.analyst_quality_details
        FROM stocks s JOIN stock_expectation_quality q ON q.stock_id=s.id
        WHERE s.futu_code IN (?,?,?,?,?,?,?) ORDER BY s.futu_code""", FOCUS_CODES)]
    summary = {"output_dir": str(output_dir), "reports": {name: len(items) for name, items in reports.items()},
               "corporate_actions": corporate_summary, "focus_stocks": len(focus), "quality_rows": len(rows),
               "quality_distribution": quality_distribution, "focus_quality": focus_quality}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    connection.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit expectation-gap data without refreshing raw target values.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "audits" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--skip-corporate-actions", action="store_true")
    args = parser.parse_args()
    run(args.output_dir, skip_corporate_actions=args.skip_corporate_actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
