"""SQLite persistence for PR6.8 minute review."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def source_trades(connection, source_run_id, symbols=None, data_cutoff=None):
    sql = """SELECT t.id source_trade_id,t.signal_id source_signal_id,s.event_id,s.observation_id,
                    s.symbol,s.first_limit_date,s.observation_date,s.trading_day_offset,
                    e.open o0,o.classification,c.industry_score,c.market_score,
                    (SELECT h.board_type FROM a_share_security_status_history h
                     WHERE h.symbol=s.symbol AND h.effective_date<=s.first_limit_date
                     ORDER BY h.effective_date DESC LIMIT 1) board_type,
                    (SELECT p.raw_value_json FROM first_limit_pullback_components p
                     WHERE p.observation_id=s.observation_id AND p.component_key='key_support') protection_json
             FROM backtest_trades t
             JOIN backtest_signals s ON s.id=t.signal_id
             JOIN first_limit_events e ON e.id=s.event_id
             JOIN first_limit_pullback_observations o ON o.id=s.observation_id
             LEFT JOIN first_limit_context_scores c ON c.observation_id=s.observation_id
                  AND c.context_scoring_version=s.context_scoring_version
             WHERE s.run_id=? AND t.entry_status='filled'"""
    args = [source_run_id]
    if data_cutoff:
        sql += " AND s.observation_date<=? AND t.actual_entry_date<=?"
        args.extend((data_cutoff, data_cutoff))
    if symbols:
        sql += " AND s.symbol IN (" + ",".join("?" for _ in symbols) + ")"
        args.extend(symbols)
    return connection.execute(sql + " ORDER BY s.symbol,s.event_id", args).fetchall()


def minute_rows(connection, symbol, start_time, end_time):
    return connection.execute(
        """SELECT * FROM first_limit_minute_bars
           WHERE symbol=? AND timeframe='1m' AND bar_time BETWEEN ? AND ?
           ORDER BY bar_time""",
        (symbol, start_time, end_time),
    ).fetchall()


def daily_limits(connection, symbol, start_date, end_date):
    return {
        row["trade_date"]: {
            "upper": row["source_upper_limit"],
            "lower": row["source_lower_limit"],
        }
        for row in connection.execute(
            """SELECT trade_date,source_upper_limit,source_lower_limit
               FROM first_limit_daily_metadata
               WHERE symbol=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date""",
            (symbol, start_date, end_date),
        )
    }


def daily_statuses(connection, symbol, start_date, end_date):
    return {
        row["effective_date"]: {
            "is_suspended": row["is_suspended"],
            "board_type": row["board_type"],
        }
        for row in connection.execute(
            """SELECT effective_date,is_suspended,board_type
               FROM a_share_security_status_history
               WHERE symbol=? AND effective_date BETWEEN ? AND ? ORDER BY effective_date""",
            (symbol, start_date, end_date),
        )
    }


def status_as_of(connection, symbol, effective_date):
    return connection.execute(
        """SELECT effective_date,is_suspended,board_type
           FROM a_share_security_status_history
           WHERE symbol=? AND effective_date<=?
           ORDER BY effective_date DESC LIMIT 1""",
        (symbol, effective_date),
    ).fetchone()


def analysis_end_date(connection, start_date, data_cutoff, session_count=3):
    rows = connection.execute(
        """SELECT trade_date FROM a_share_trading_calendar
           WHERE market='CN' AND is_open=1 AND trade_date BETWEEN ? AND ?
           ORDER BY trade_date LIMIT ?""",
        (start_date, data_cutoff, session_count),
    ).fetchall()
    return rows[-1][0] if rows else data_cutoff


def trading_dates(connection, start_date, data_cutoff, session_count=3):
    return [
        row[0]
        for row in connection.execute(
            """SELECT trade_date FROM a_share_trading_calendar
               WHERE market='CN' AND is_open=1 AND trade_date BETWEEN ? AND ?
               ORDER BY trade_date LIMIT ?""",
            (start_date, data_cutoff, session_count),
        )
    ]


def create_run(
    connection, run_id, source_run_id, params, parameter_hash, version, cutoff, planned_count
):
    stamp = now()
    connection.execute(
        """INSERT INTO minute_review_runs(
             run_id,source_backtest_run_id,parameters_json,parameter_hash,status,review_version,
             data_cutoff,planned_count,started_at,created_at,updated_at)
           VALUES(?,?,?,?,'running',?,?,?,?,?,?)""",
        (
            run_id, source_run_id, dump(params), parameter_hash, version, cutoff,
            planned_count, stamp, stamp, stamp,
        ),
    )


def resume_run(connection, run_id, parameter_hash):
    row = connection.execute("SELECT * FROM minute_review_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise LookupError("minute review run not found")
    if row["parameter_hash"] != parameter_hash:
        raise ValueError("resume parameters do not match original minute review run")
    connection.execute(
        "UPDATE minute_review_runs SET status='running',finished_at=NULL,last_error=NULL,updated_at=? WHERE run_id=?",
        (now(), run_id),
    )
    return row


def completed_trade_ids(connection, run_id):
    return {
        row[0]
        for row in connection.execute(
            """SELECT source_trade_id FROM minute_review_items
               WHERE run_id=? AND status IN ('success','indeterminate','unresolved','skipped')""",
            (run_id,),
        )
    }


def delete_result(connection, run_id, source_trade_id):
    connection.execute(
        "DELETE FROM minute_review_results WHERE run_id=? AND source_trade_id=?",
        (run_id, source_trade_id),
    )


def save_result(connection, run_id, source, confirmation, quality_status, groups, audit, stops):
    stamp = now()
    cursor = connection.execute(
        """INSERT INTO minute_review_results(
             run_id,source_trade_id,source_signal_id,event_id,observation_id,symbol,first_limit_date,
             observation_date,o0,confirmation_status,confirmation_reason,confirmation_time,
             entry_price_raw,entry_price,entry_cost,stop_distance,data_quality_status,classification,
             trading_day_offset,board_bucket,protection_type,market_environment,industry_environment,
             year,audit_json,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, source["source_trade_id"], source["source_signal_id"], source["event_id"],
            source["observation_id"], source["symbol"], source["first_limit_date"],
            source["observation_date"], source["o0"], confirmation.status, confirmation.reason,
            confirmation.confirmation_time, _number(confirmation.entry_price_raw),
            _number(confirmation.entry_price), _number(confirmation.entry_cost),
            _number(confirmation.stop_distance), quality_status, groups["classification"],
            groups["trading_day_offset"], groups["board_bucket"], groups["protection_type"],
            groups["market_environment"], groups["industry_environment"], groups["year"],
            dump(audit), stamp, stamp,
        ),
    )
    result_id = cursor.lastrowid
    for rule, result in stops.items():
        connection.execute(
            """INSERT INTO minute_review_stop_results(
                 review_result_id,stop_rule,status,trigger_time,trigger_price,trigger_reason,exit_time,
                 exit_price_raw,exit_price,exit_cost,gross_return,net_return,max_drawdown,
                 intraday_path_ambiguous,delay_minutes,audit_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                result_id, rule, result["status"], result["trigger_time"], _number(result["trigger_price"]),
                result["trigger_reason"], result["exit_time"], _number(result["exit_price_raw"]),
                _number(result["exit_price"]), _number(result["exit_cost"]),
                _number(result["gross_return"]), _number(result["net_return"]),
                _number(result["max_drawdown"]), int(bool(result["intraday_path_ambiguous"])),
                result["delay_minutes"], dump(result.get("audit", {})),
            ),
        )
    return result_id


def save_item(connection, run_id, source_trade_id, symbol, status, result_id=None, error=None):
    stamp = now()
    connection.execute(
        """INSERT INTO minute_review_items(
             run_id,source_trade_id,symbol,status,result_id,error_type,last_error,started_at,finished_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id,source_trade_id) DO UPDATE SET
             status=excluded.status,result_id=excluded.result_id,error_type=excluded.error_type,
             last_error=excluded.last_error,finished_at=excluded.finished_at,updated_at=excluded.updated_at""",
        (
            run_id, source_trade_id, symbol, status, result_id,
            type(error).__name__ if error else None, str(error)[:1000] if error else None,
            stamp, stamp, stamp,
        ),
    )


def finish_run(connection, run_id, forced_status=None, error=None):
    row = connection.execute(
        """SELECT COUNT(*) planned,
                  COALESCE(SUM(status='success'),0) success,
                  COALESCE(SUM(status='indeterminate'),0) indeterminate,
                  COALESCE(SUM(status='unresolved'),0) unresolved,
                  COALESCE(SUM(status='skipped'),0) skipped,
                  COALESCE(SUM(status='failed'),0) failed
           FROM minute_review_items WHERE run_id=?""",
        (run_id,),
    ).fetchone()
    planned_count = connection.execute(
        "SELECT planned_count FROM minute_review_runs WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    status = forced_status or (
        "failed" if row["planned"] and row["failed"] == row["planned"]
        else "partial" if row["failed"] or row["indeterminate"] or row["unresolved"]
        else "success"
    )
    stamp = now()
    connection.execute(
        """UPDATE minute_review_runs SET status=?,planned_count=?,success_count=?,indeterminate_count=?,
                  unresolved_count=?,skipped_count=?,failure_count=?,last_error=?,finished_at=?,updated_at=?
           WHERE run_id=?""",
        (
            status, planned_count, row["success"], row["indeterminate"], row["unresolved"],
            row["skipped"], row["failed"], str(error)[:1000] if error else None, stamp, stamp, run_id,
        ),
    )
    return status


def save_metrics(connection, run_id, scope, group_key, group_value, metrics):
    connection.execute(
        """INSERT INTO minute_review_metrics(run_id,scope,group_key,group_value,metrics_json)
           VALUES(?,?,?,?,?)
           ON CONFLICT(run_id,scope,group_key,group_value)
           DO UPDATE SET metrics_json=excluded.metrics_json""",
        (run_id, scope, group_key, str(group_value), dump(metrics)),
    )


def _number(value):
    return None if value is None else float(Decimal(str(value)))
