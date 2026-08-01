"""SQLite queries and persistence for PR6.9 daily candidate snapshots."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dump(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def source_events(connection, trade_date, detection_version, symbols=None):
    sql = """SELECT * FROM first_limit_events
             WHERE trade_date<=? AND detection_version=?
               AND detection_status='detected' AND is_first_limit=1"""
    args = [trade_date, detection_version]
    if symbols:
        sql += " AND symbol IN (" + ",".join("?" for _ in symbols) + ")"
        args.extend(symbols)
    return connection.execute(sql + " ORDER BY trade_date,symbol,id", args).fetchall()


def calendar_rows(connection, start_date, end_date):
    return connection.execute(
        """SELECT trade_date,is_open FROM a_share_trading_calendar
           WHERE market='CN' AND trade_date BETWEEN ? AND ? ORDER BY trade_date""",
        (start_date, end_date),
    ).fetchall()


def status_as_of(connection, symbol, day):
    return connection.execute(
        """SELECT * FROM a_share_security_status_history
           WHERE symbol=? AND effective_date<=?
           ORDER BY effective_date DESC LIMIT 1""",
        (symbol, day),
    ).fetchone()


def bars_through(connection, symbol, event_date, trade_date):
    rows = connection.execute(
        """SELECT * FROM (
             SELECT trade_date,open,high,low,close,volume,amount
             FROM a_share_daily_bars
             WHERE stock_code=? AND adjustment='none' AND trade_date<=?
             ORDER BY trade_date DESC LIMIT 40
           ) ORDER BY trade_date""",
        (symbol.split(".")[0], trade_date),
    ).fetchall()
    return [dict(row) for row in rows if row["trade_date"] >= event_date or len(rows) <= 40]


def context_for_event(
    connection,
    event_id,
    trade_date,
    detection_version,
    pullback_version,
    context_version,
    *,
    exact_date,
):
    comparator = "=" if exact_date else "<"
    return connection.execute(
        f"""SELECT c.*,o.classification,o.pool_status,o.is_eliminated
            FROM first_limit_context_scores c
            JOIN first_limit_pullback_observations o ON o.id=c.observation_id
            WHERE c.event_id=? AND c.observation_date {comparator} ?
              AND c.detection_version=? AND c.pullback_version=?
              AND c.context_scoring_version=?
            ORDER BY c.observation_date DESC LIMIT 1""",
        (
            event_id, trade_date, detection_version, pullback_version,
            context_version,
        ),
    ).fetchone()


def minute_rows(connection, symbol, start_time, end_time):
    return connection.execute(
        """SELECT * FROM first_limit_minute_bars
           WHERE symbol=? AND timeframe='1m' AND bar_time BETWEEN ? AND ?
           ORDER BY bar_time""",
        (symbol, start_time, end_time),
    )


def previously_eliminated(connection, event_id, trade_date):
    return connection.execute(
        """SELECT 1 FROM daily_candidate_snapshots
           WHERE first_limit_event_id=? AND trade_date<?
             AND lifecycle_status='eliminated' LIMIT 1""",
        (event_id, trade_date),
    ).fetchone() is not None


def terminal_before(connection, event_id, trade_date):
    return connection.execute(
        """SELECT 1 FROM daily_candidate_snapshots
           WHERE first_limit_event_id=? AND trade_date<?
             AND lifecycle_status IN ('eliminated','expired') LIMIT 1""",
        (event_id, trade_date),
    ).fetchone() is not None


def preview_snapshot(connection, event_id, trade_date, strategy_version):
    row = connection.execute(
        """SELECT s.* FROM daily_candidate_snapshots s
           JOIN daily_candidate_runs r ON r.run_id=s.run_id
           WHERE s.first_limit_event_id=? AND s.trade_date=?
             AND s.stage='tail_preview' AND s.strategy_version=?
             AND r.status IN ('success','partial')
           ORDER BY r.finished_at DESC,s.id DESC LIMIT 1""",
        (event_id, trade_date, strategy_version),
    ).fetchone()
    return dict(row) if row else None


def find_run_by_hash(connection, trade_date, stage, parameter_hash):
    return connection.execute(
        """SELECT * FROM daily_candidate_runs
           WHERE trade_date=? AND stage=? AND parameter_hash=?""",
        (trade_date, stage, parameter_hash),
    ).fetchone()


def create_run(
    connection, run_id, params, parameter_hash, planned_count, detection_complete
):
    stamp = now()
    connection.execute(
        """INSERT INTO daily_candidate_runs(
             run_id,trade_date,stage,as_of,data_cutoff,strategy_version,detection_version,
             pullback_version,context_version,parameters_json,parameter_hash,status,
             detection_complete,planned_count,started_at,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,'running',?,?,?,?,?)""",
        (
            run_id, params["trade_date"], params["stage"], params["as_of"],
            params["data_cutoff"], params["strategy_version"],
            params["versions"]["detection"], params["versions"]["pullback"],
            params["versions"]["context"], dump(params), parameter_hash,
            int(bool(detection_complete)), planned_count, stamp, stamp, stamp,
        ),
    )


def claim_run(connection, run_id, params, parameter_hash):
    """Atomically claim the formal run identity without a second API ledger."""
    stamp = now()
    cursor = connection.execute(
        """INSERT OR IGNORE INTO daily_candidate_runs(
             run_id,trade_date,stage,as_of,data_cutoff,strategy_version,detection_version,
             pullback_version,context_version,parameters_json,parameter_hash,status,
             detection_complete,planned_count,started_at,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,'running',0,0,?,?,?)""",
        (
            run_id, params["trade_date"], params["stage"], params["as_of"],
            params["data_cutoff"], params["strategy_version"],
            params["versions"]["detection"], params["versions"]["pullback"],
            params["versions"]["context"], dump(params), parameter_hash,
            stamp, stamp, stamp,
        ),
    )
    row = find_run_by_hash(
        connection, params["trade_date"], params["stage"], parameter_hash
    )
    if row is None:
        raise RuntimeError("unable to claim daily candidate run identity")
    return row, cursor.rowcount == 1


def initialize_claimed_run(
    connection, run_id, events, detection_complete
):
    """Freeze source items after an API claimant has obtained execution rights."""
    stamp = now()
    connection.execute(
        """UPDATE daily_candidate_runs
           SET detection_complete=?,planned_count=?,updated_at=?
           WHERE run_id=?""",
        (int(bool(detection_complete)), len(events), stamp, run_id),
    )
    initialize_items(connection, run_id, events)


def initialize_items(connection, run_id, events):
    stamp = now()
    connection.executemany(
        """INSERT INTO daily_candidate_items(
             run_id,first_limit_event_id,symbol,status,candidate_id,attempt,
             started_at,updated_at)
           VALUES(?,?,?,'pending',NULL,0,?,?)""",
        [
            (run_id, event["id"], event["symbol"], stamp, stamp)
            for event in events
        ],
    )


def scoped_event_ids(connection, run_id):
    return {
        row[0]
        for row in connection.execute(
            """SELECT first_limit_event_id FROM daily_candidate_items
               WHERE run_id=?""",
            (run_id,),
        )
    }


def resume_run(connection, run_id, parameter_hash):
    row = connection.execute(
        "SELECT * FROM daily_candidate_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise LookupError("daily candidate run not found")
    if row["parameter_hash"] != parameter_hash:
        raise ValueError("resume parameters do not match original daily candidate run")
    connection.execute(
        """UPDATE daily_candidate_runs
           SET status='running',finished_at=NULL,last_error=NULL,updated_at=?
           WHERE run_id=?""",
        (now(), run_id),
    )
    return row


def completed_event_ids(connection, run_id):
    return {
        row[0]
        for row in connection.execute(
            """SELECT first_limit_event_id FROM daily_candidate_items
               WHERE run_id=? AND status IN ('success','indeterminate','skipped')""",
            (run_id,),
        )
    }


def delete_candidate(connection, run_id, event_id):
    connection.execute(
        """DELETE FROM daily_candidate_snapshots
           WHERE run_id=? AND first_limit_event_id=?""",
        (run_id, event_id),
    )


def save_candidate(
    connection, run_id, event, trade_date, stage, decision, versions,
    strategy_version, preview, change_type, audit,
):
    stamp = now()
    industry = audit.get("industry_context") or {}
    membership = industry.get("membership") or {}
    effective = industry.get("effective") or {}
    cursor = connection.execute(
        """INSERT INTO daily_candidate_snapshots(
             run_id,first_limit_event_id,trade_date,stage,symbol,observation_day,
             lifecycle_status,candidate_grade,score,preview_candidate_id,change_type,
             detection_version,pullback_version,context_version,strategy_version,
             primary_reasons_json,audit_json,created_at,updated_at,
             sw_level1_code,sw_level2_code,sw_level3_code,
             effective_industry_level,effective_industry_code,industry_context_status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, event["id"], trade_date, stage, event["symbol"],
            decision.observation_day, decision.lifecycle_status,
            decision.candidate_grade,
            float(decision.score) if decision.score is not None else None,
            preview["id"] if preview else None, change_type,
            versions["detection"], versions["pullback"], versions["context"],
            strategy_version, dump(decision.primary_reasons), dump(audit), stamp, stamp,
            membership.get("level1_code"), membership.get("level2_code"),
            membership.get("level3_code"), effective.get("effective_level"),
            effective.get("effective_industry_code"), industry.get("status"),
        ),
    )
    candidate_id = cursor.lastrowid
    for ordinal, evidence in enumerate(decision.evidence):
        connection.execute(
            """INSERT INTO daily_candidate_evidence(
                 candidate_id,rule_code,result,actual_value,threshold_value,unit,
                 source_date,source_time,reason_code,display_text,ordinal)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate_id, evidence.rule_code, evidence.result,
                dump(evidence.actual_value) if evidence.actual_value is not None else None,
                dump(evidence.threshold_value) if evidence.threshold_value is not None else None,
                evidence.unit, evidence.source_date, evidence.source_time,
                evidence.reason_code, evidence.display_text, ordinal,
            ),
        )
    if industry:
        connection.execute(
            """INSERT INTO daily_candidate_evidence(
                 candidate_id,rule_code,result,actual_value,threshold_value,unit,
                 source_date,source_time,reason_code,display_text,ordinal)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate_id, "INDUSTRY_CONTEXT",
                "pass" if industry.get("status") == "complete" else "unknown",
                dump(industry), None, None,
                str(industry.get("first_limit_score_date") or "") or None,
                None, industry.get("status"), "首板行业上下文",
                len(decision.evidence),
            ),
        )
    return candidate_id


def save_item(
    connection, run_id, event_id, symbol, status, candidate_id=None, error=None
):
    stamp = now()
    previous = connection.execute(
        """SELECT attempt FROM daily_candidate_items
           WHERE run_id=? AND first_limit_event_id=?""",
        (run_id, event_id),
    ).fetchone()
    attempt = (previous["attempt"] if previous else 0) + 1
    connection.execute(
        """INSERT INTO daily_candidate_items(
             run_id,first_limit_event_id,symbol,status,candidate_id,attempt,error_type,
             last_error,started_at,finished_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id,first_limit_event_id) DO UPDATE SET
             status=excluded.status,candidate_id=excluded.candidate_id,
             attempt=excluded.attempt,error_type=excluded.error_type,
             last_error=excluded.last_error,finished_at=excluded.finished_at,
             updated_at=excluded.updated_at""",
        (
            run_id, event_id, symbol, status, candidate_id, attempt,
            type(error).__name__ if error else None,
            str(error)[:1000] if error else None, stamp, stamp, stamp,
        ),
    )


def finish_run(connection, run_id, forced_status=None, error=None):
    counts = connection.execute(
        """SELECT COUNT(*) item_count,
                  COALESCE(SUM(status='success'),0) success,
                  COALESCE(SUM(status='indeterminate'),0) indeterminate,
                  COALESCE(SUM(status='skipped'),0) skipped,
                  COALESCE(SUM(status='failed'),0) failed,
                  COALESCE(SUM(status='pending'),0) pending
           FROM daily_candidate_items WHERE run_id=?""",
        (run_id,),
    ).fetchone()
    planned = connection.execute(
        "SELECT planned_count FROM daily_candidate_runs WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    status = forced_status or (
        "failed" if planned and counts["failed"] == planned
        else "partial" if counts["failed"] or counts["indeterminate"] or counts["pending"]
        else "success"
    )
    stamp = now()
    connection.execute(
        """UPDATE daily_candidate_runs SET status=?,success_count=?,
                  indeterminate_count=?,skipped_count=?,failure_count=?,last_error=?,
                  finished_at=?,updated_at=? WHERE run_id=?""",
        (
            status, counts["success"], counts["indeterminate"], counts["skipped"],
            counts["failed"], str(error)[:1000] if error else None,
            stamp, stamp, run_id,
        ),
    )
    return status


def run_row(connection, run_id):
    return connection.execute(
        "SELECT * FROM daily_candidate_runs WHERE run_id=?", (run_id,)
    ).fetchone()


def snapshots(connection, run_id):
    return connection.execute(
        """SELECT * FROM daily_candidate_snapshots WHERE run_id=?
           ORDER BY CASE candidate_grade WHEN 'S' THEN 0 WHEN 'A' THEN 1
                    WHEN 'B' THEN 2 ELSE 3 END,
                    score DESC,symbol,first_limit_event_id""",
        (run_id,),
    ).fetchall()


def evidence_for(connection, candidate_id):
    return connection.execute(
        """SELECT rule_code,result,actual_value,threshold_value,unit,source_date,
                  source_time,reason_code,display_text
           FROM daily_candidate_evidence WHERE candidate_id=? ORDER BY ordinal,rule_code""",
        (candidate_id,),
    ).fetchall()


def failed_items(connection, run_id):
    return connection.execute(
        """SELECT first_limit_event_id,symbol,error_type,last_error
           FROM daily_candidate_items WHERE run_id=? AND status='failed'
           ORDER BY symbol,first_limit_event_id""",
        (run_id,),
    ).fetchall()
