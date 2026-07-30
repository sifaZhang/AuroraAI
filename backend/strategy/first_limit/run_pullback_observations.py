"""Reusable PR6.5 pullback observation runner.

The pure strategy rules remain in :mod:`pullback`; this module only assembles
already-cached daily inputs and persists the formal observation audit.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import date, datetime, timezone

from . import pullback


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _events(connection, start, end, detection_version, scoring_version, symbols):
    sql = """SELECT e.*,q.scoring_version
             FROM first_limit_events e
             JOIN first_limit_quality_scores q ON q.event_id=e.id
             WHERE e.trade_date BETWEEN ? AND ?
               AND e.detection_version=? AND q.scoring_version=?
               AND e.detection_status='detected' AND e.is_first_limit=1"""
    args = [str(start), str(end), detection_version, scoring_version]
    if symbols:
        sql += " AND e.symbol IN (" + ",".join("?" for _ in symbols) + ")"
        args.extend(symbols)
    return connection.execute(sql + " ORDER BY e.trade_date,e.symbol", args).fetchall()


def _open_dates(connection, start, end):
    return [
        row[0] for row in connection.execute(
            """SELECT trade_date FROM a_share_trading_calendar
               WHERE market='CN' AND is_open=1 AND trade_date BETWEEN ? AND ?
               ORDER BY trade_date""",
            (str(start), str(end)),
        )
    ]


def _bar(connection, symbol, day):
    return connection.execute(
        """SELECT * FROM a_share_daily_bars
           WHERE stock_code=? AND adjustment='none' AND trade_date=?""",
        (symbol.split(".")[0], day),
    ).fetchone()


def _inputs(connection, event, observation_date, open_dates):
    eligible_dates = [
        day for day in open_dates
        if event["trade_date"] < day <= observation_date
    ]
    bars = [_bar(connection, event["symbol"], day) for day in eligible_dates]
    if not bars or any(row is None for row in bars):
        raise LookupError("missing pullback daily bar")
    target = bars[-1]
    prior_volumes = [
        row[0] for row in connection.execute(
            """SELECT volume FROM a_share_daily_bars
               WHERE stock_code=? AND adjustment='none' AND trade_date<?
               ORDER BY trade_date DESC LIMIT 5""",
            (event["symbol"].split(".")[0], observation_date),
        ).fetchall()[::-1]
    ]
    parts = [
        pullback.max_drawdown(event["close"], [row["low"] for row in bars]),
        pullback.close_to_c0(event["close"], target["close"]),
        pullback.volume_risks(
            target["open"], target["close"],
            bars[-2]["close"] if len(bars) > 1 else event["close"],
            target["volume"], prior_volumes,
        ),
        pullback.support(event["open"], [row["low"] for row in bars],
                         [row["close"] for row in bars]),
    ]
    contraction = pullback.volume(
        _bar(connection, event["symbol"], event["trade_date"])["volume"],
        target["volume"], [row["volume"] for row in bars],
    )
    parts.extend(contraction if isinstance(contraction, tuple) else [contraction])
    parts.extend([
        pullback.close_location(
            target["low"], target["high"], target["close"]
        ),
        pullback.rhythm([event["close"], *[row["close"] for row in bars]]),
        pullback.ma5_status([
            row[0] for row in connection.execute(
                """SELECT close FROM a_share_daily_bars
                   WHERE stock_code=? AND adjustment='none' AND trade_date<=?
                   ORDER BY trade_date DESC LIMIT 6""",
                (event["symbol"].split(".")[0], observation_date),
            ).fetchall()[::-1]
        ]),
    ])
    classification, class_reasons = pullback.classify(
        event["open"], event["close"], [row["low"] for row in bars]
    )
    return bars, parts, classification, class_reasons


def _save(connection, event, observation_date, offset, pullback_version,
          parts, classification, class_reasons):
    summary = pullback.aggregate(parts)
    eliminated = classification == "ELIMINATED" or any(
        part.status == "fail" and part.key in {
            "max_drawdown", "close_to_c0", "volume_risk"
        }
        for part in parts
    )
    elimination_reasons = sorted({
        *class_reasons,
        *(reason for part in parts if part.status == "fail" for reason in part.reasons),
    })
    pool_status = (
        "eliminated" if eliminated else "candidate"
        if classification in {"A1", "A2"} else "watch"
        if classification in {"B", "DEEP_WATCH"} else "indeterminate"
    )
    status = (
        "indeterminate" if classification == "INDETERMINATE"
        or summary["status"] == "indeterminate" else "fail"
        if eliminated else "pass"
    )
    stamp = _now()
    connection.execute(
        """INSERT INTO first_limit_pullback_observations(
             event_id,symbol,first_limit_date,observation_date,trading_day_offset,
             detection_version,scoring_version,pullback_version,
             observation_status,classification,pool_status,is_eliminated,
             eliminated_at,elimination_reasons_json,earned_score,
             theoretical_max_score,determinable_max_score,coverage_ratio,
             is_complete,is_approximate,reasons_json,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(event_id,observation_date,pullback_version) DO UPDATE SET
             observation_status=excluded.observation_status,
             classification=excluded.classification,pool_status=excluded.pool_status,
             is_eliminated=excluded.is_eliminated,
             eliminated_at=excluded.eliminated_at,
             elimination_reasons_json=excluded.elimination_reasons_json,
             earned_score=excluded.earned_score,
             determinable_max_score=excluded.determinable_max_score,
             coverage_ratio=excluded.coverage_ratio,
             is_complete=excluded.is_complete,reasons_json=excluded.reasons_json,
             updated_at=excluded.updated_at""",
        (
            event["id"], event["symbol"], event["trade_date"], observation_date,
            offset, event["detection_version"], event["scoring_version"],
            pullback_version, status, classification, pool_status, int(eliminated),
            stamp if eliminated else None, _dump(elimination_reasons),
            str(summary["earned_score"]), str(summary["theoretical_max_score"]),
            str(summary["determinable_max_score"]), str(summary["coverage_ratio"]),
            int(summary["is_complete"]), 0, _dump(summary["reasons"]), stamp, stamp,
        ),
    )
    observation_id = connection.execute(
        """SELECT id FROM first_limit_pullback_observations
           WHERE event_id=? AND observation_date=? AND pullback_version=?""",
        (event["id"], observation_date, pullback_version),
    ).fetchone()[0]
    for part in parts:
        connection.execute(
            """INSERT INTO first_limit_pullback_components(
                 observation_id,component_key,component_status,earned_score,
                 max_score,raw_value_json,reasons_json,source_table,source_date,
                 is_approximate)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(observation_id,component_key) DO UPDATE SET
                 component_status=excluded.component_status,
                 earned_score=excluded.earned_score,max_score=excluded.max_score,
                 raw_value_json=excluded.raw_value_json,
                 reasons_json=excluded.reasons_json,
                 source_date=excluded.source_date,
                 is_approximate=excluded.is_approximate""",
            (
                observation_id, part.key, part.status,
                None if part.score is None else str(part.score), str(part.maximum),
                _dump(part.raw), _dump(part.reasons), "a_share_daily_bars",
                observation_date, int(part.approximate),
            ),
        )
    return observation_id, status


def run_pullback_observations(
    connection, *, event_start, event_end, through_date, symbols=None,
    detection_version="first_limit_v1",
    scoring_version="first_limit_quality_v1",
    pullback_version=pullback.VERSION,
    dry_run=False,
):
    events = _events(
        connection, event_start, event_end, detection_version, scoring_version,
        symbols,
    )
    dates = _open_dates(connection, event_start, through_date)
    items = []
    for event in events:
        following = [day for day in dates if day > event["trade_date"]]
        items.extend(
            (event, day, offset)
            for offset, day in enumerate(following[:5], 1)
            if 2 <= offset <= 5 and day <= str(through_date)
        )
    if dry_run:
        return {"run_id": "dry-run", "status": "success", "planned": len(items)}
    run_id = uuid.uuid4().hex
    stamp = _now()
    params = {
        "event_start": str(event_start), "event_end": str(event_end),
        "through_date": str(through_date), "symbols": symbols,
        "detection_version": detection_version,
        "scoring_version": scoring_version,
        "pullback_version": pullback_version,
    }
    connection.execute(
        """INSERT INTO first_limit_pullback_runs(
             run_id,parameters_json,status,is_dry_run,started_at,created_at,updated_at)
           VALUES(?,?,'running',0,?,?,?)""",
        (run_id, _dump(params), stamp, stamp, stamp),
    )
    counts = Counter()
    last_error = None
    for event, observation_date, offset in items:
        key = f"{event['id']}:{observation_date}"
        try:
            _bars, parts, classification, class_reasons = _inputs(
                connection, event, observation_date, dates
            )
            with connection:
                observation_id, status = _save(
                    connection, event, observation_date, offset,
                    pullback_version, parts, classification, class_reasons,
                )
                connection.execute(
                    """INSERT INTO first_limit_pullback_run_items(
                         run_id,item_key,status,observation_id,result_json,
                         last_error,updated_at)
                       VALUES(?,?,'success',?,?,NULL,?)""",
                    (
                        run_id, key, observation_id,
                        _dump({"observation_status": status}), _now(),
                    ),
                )
            counts["success"] += 1
            counts[status] += 1
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            with connection:
                connection.execute(
                    """INSERT INTO first_limit_pullback_run_items(
                         run_id,item_key,status,last_error,updated_at)
                       VALUES(?,?,'failed',?,?)""",
                    (run_id, key, last_error[:1000], _now()),
                )
            counts["failed"] += 1
    status = (
        "failed" if items and counts["failed"] == len(items)
        else "partial" if counts["failed"] or counts["indeterminate"]
        else "success"
    )
    stamp = _now()
    connection.execute(
        """UPDATE first_limit_pullback_runs
           SET status=?,planned_count=?,success_count=?,failure_count=?,
               last_error=?,finished_at=?,updated_at=? WHERE run_id=?""",
        (
            status, len(items), counts["success"], counts["failed"],
            last_error, stamp, stamp, run_id,
        ),
    )
    connection.commit()
    return {"run_id": run_id, "status": status, "planned": len(items), **counts}
