"""Parameterized read models for the PR6.10 first-limit API."""
from __future__ import annotations

from collections.abc import Iterable


SORT_COLUMNS = {
    "grade_rank": "grade_rank",
    "base_score": "s.score",
    "symbol": "s.symbol",
    "first_limit_event_id": "s.first_limit_event_id",
    "created_at": "s.created_at",
}


def _in_filter(sql: list[str], args: list[object], column: str, values: Iterable[str]):
    selected = tuple(values)
    if selected:
        sql.append(f"{column} IN ({','.join('?' for _ in selected)})")
        args.extend(selected)


def latest_run(connection, trade_date, stage):
    return connection.execute(
        """SELECT * FROM daily_candidate_runs
           WHERE trade_date=? AND stage=?
           ORDER BY created_at DESC,run_id DESC LIMIT 1""",
        (trade_date, stage),
    ).fetchone()


def candidate_page(
    connection,
    *,
    run_id,
    grades=(),
    lifecycles=(),
    symbol=None,
    change_type=None,
    include_unknown=True,
    sort="grade_rank",
    order="asc",
    limit=100,
    offset=0,
):
    where = ["s.run_id=?"]
    args: list[object] = [run_id]
    selected_grades = tuple(grades)
    stored_grades = tuple(value for value in selected_grades if value != "none")
    grade_clauses = []
    if stored_grades:
        grade_clauses.append(
            f"s.candidate_grade IN ({','.join('?' for _ in stored_grades)})"
        )
        args.extend(stored_grades)
    if "none" in selected_grades:
        grade_clauses.append("s.candidate_grade IS NULL")
    if grade_clauses:
        where.append(f"({' OR '.join(grade_clauses)})")
    _in_filter(where, args, "s.lifecycle_status", lifecycles)
    if symbol:
        where.append("s.symbol=?")
        args.append(symbol)
    if change_type:
        where.append("s.change_type=?")
        args.append(change_type)
    if not include_unknown:
        where.append("s.lifecycle_status<>'indeterminate'")
    predicate = " AND ".join(where)
    total = connection.execute(
        f"SELECT COUNT(*) FROM daily_candidate_snapshots s WHERE {predicate}", args
    ).fetchone()[0]
    sort_column = SORT_COLUMNS[sort]
    direction = "ASC" if order == "asc" else "DESC"
    rows = connection.execute(
        f"""SELECT
              s.id candidate_id,s.run_id,s.first_limit_event_id,s.symbol,
              s.trade_date,s.stage,r.as_of,s.observation_day,
              s.lifecycle_status lifecycle,s.candidate_grade grade,
              NULL base_grade,s.score base_score,s.change_type,
              json_extract(s.primary_reasons_json,'$[0]') reason_code,
              (SELECT e.display_text FROM daily_candidate_evidence e
               WHERE e.candidate_id=s.id
               ORDER BY e.ordinal,e.rule_code LIMIT 1) display_text,
              e.trade_date first_limit_date,s.preview_candidate_id,
              s.created_at,s.updated_at,
              CASE s.candidate_grade WHEN 'S' THEN 0 WHEN 'A' THEN 1
                   WHEN 'B' THEN 2 ELSE 3 END grade_rank
            FROM daily_candidate_snapshots s
            JOIN daily_candidate_runs r ON r.run_id=s.run_id
            JOIN first_limit_events e ON e.id=s.first_limit_event_id
            WHERE {predicate}
            ORDER BY {sort_column} {direction},
                     s.score DESC NULLS LAST,s.symbol ASC,s.first_limit_event_id ASC
            LIMIT ? OFFSET ?""",
        [*args, limit, offset],
    ).fetchall()
    return total, rows


def candidate(connection, candidate_id):
    return connection.execute(
        """SELECT
             s.id candidate_id,s.run_id,s.first_limit_event_id,s.symbol,
             s.trade_date,s.stage,r.as_of,s.observation_day,
             s.lifecycle_status lifecycle,s.candidate_grade grade,
             NULL base_grade,s.score base_score,s.change_type,
             json_extract(s.primary_reasons_json,'$[0]') reason_code,
             (SELECT e2.display_text FROM daily_candidate_evidence e2
              WHERE e2.candidate_id=s.id
              ORDER BY e2.ordinal,e2.rule_code LIMIT 1) display_text,
             e.trade_date first_limit_date,s.preview_candidate_id,
             s.created_at,s.updated_at
           FROM daily_candidate_snapshots s
           JOIN daily_candidate_runs r ON r.run_id=s.run_id
           JOIN first_limit_events e ON e.id=s.first_limit_event_id
           WHERE s.id=?""",
        (candidate_id,),
    ).fetchone()


def evidence(connection, candidate_id):
    return connection.execute(
        """SELECT rule_code,result,actual_value,threshold_value,unit,source_date,
                  source_time,reason_code,display_text,ordinal
           FROM daily_candidate_evidence WHERE candidate_id=?
           ORDER BY ordinal,rule_code""",
        (candidate_id,),
    ).fetchall()


def run(connection, run_id):
    return connection.execute(
        """SELECT r.*,
             (SELECT COUNT(*) FROM daily_candidate_items i
              WHERE i.run_id=r.run_id AND i.status='pending') pending_count,
             (SELECT COUNT(*) FROM daily_candidate_snapshots s
              WHERE s.run_id=r.run_id AND s.lifecycle_status='confirmed') confirmed_count,
             (SELECT COUNT(*) FROM daily_candidate_snapshots s
              WHERE s.run_id=r.run_id AND s.lifecycle_status='eliminated') eliminated_count,
             (SELECT COUNT(*) FROM daily_candidate_snapshots s
              WHERE s.run_id=r.run_id AND s.lifecycle_status='indeterminate') snapshot_indeterminate_count
           FROM daily_candidate_runs r WHERE r.run_id=?""",
        (run_id,),
    ).fetchone()


def run_page(
    connection,
    *,
    trade_date=None,
    stage=None,
    statuses=(),
    strategy_version=None,
    limit=100,
    offset=0,
):
    where = ["1=1"]
    args: list[object] = []
    if trade_date:
        where.append("r.trade_date=?")
        args.append(trade_date)
    if stage:
        where.append("r.stage=?")
        args.append(stage)
    _in_filter(where, args, "r.status", statuses)
    if strategy_version:
        where.append("r.strategy_version=?")
        args.append(strategy_version)
    predicate = " AND ".join(where)
    total = connection.execute(
        f"SELECT COUNT(*) FROM daily_candidate_runs r WHERE {predicate}", args
    ).fetchone()[0]
    rows = connection.execute(
        f"""SELECT r.*,
             (SELECT COUNT(*) FROM daily_candidate_items i
              WHERE i.run_id=r.run_id AND i.status='pending') pending_count,
             (SELECT COUNT(*) FROM daily_candidate_snapshots s
              WHERE s.run_id=r.run_id AND s.lifecycle_status='confirmed') confirmed_count,
             (SELECT COUNT(*) FROM daily_candidate_snapshots s
              WHERE s.run_id=r.run_id AND s.lifecycle_status='eliminated') eliminated_count,
             (SELECT COUNT(*) FROM daily_candidate_snapshots s
              WHERE s.run_id=r.run_id AND s.lifecycle_status='indeterminate') snapshot_indeterminate_count
           FROM daily_candidate_runs r WHERE {predicate}
           ORDER BY r.created_at DESC,r.run_id DESC LIMIT ? OFFSET ?""",
        [*args, limit, offset],
    ).fetchall()
    return total, rows


def run_groups(connection, run_id):
    item_counts = {
        row["status"]: row["count"]
        for row in connection.execute(
            """SELECT status,COUNT(*) count FROM daily_candidate_items
               WHERE run_id=? GROUP BY status""",
            (run_id,),
        )
    }
    grade_counts = {
        (row["candidate_grade"] if row["candidate_grade"] is not None else "unknown"):
        row["count"]
        for row in connection.execute(
            """SELECT candidate_grade,COUNT(*) count FROM daily_candidate_snapshots
               WHERE run_id=? GROUP BY candidate_grade""",
            (run_id,),
        )
    }
    lifecycle_counts = {
        row["lifecycle_status"]: row["count"]
        for row in connection.execute(
            """SELECT lifecycle_status,COUNT(*) count
               FROM daily_candidate_snapshots WHERE run_id=?
               GROUP BY lifecycle_status""",
            (run_id,),
        )
    }
    failures = connection.execute(
        """SELECT first_limit_event_id,symbol,error_type,last_error
           FROM daily_candidate_items WHERE run_id=? AND status='failed'
           ORDER BY symbol,first_limit_event_id LIMIT 20""",
        (run_id,),
    ).fetchall()
    return item_counts, grade_counts, lifecycle_counts, failures


def item_page(
    connection, *, run_id, statuses=(), symbol=None, limit=100, offset=0
):
    where = ["i.run_id=?"]
    args: list[object] = [run_id]
    _in_filter(where, args, "i.status", statuses)
    if symbol:
        where.append("i.symbol=?")
        args.append(symbol)
    predicate = " AND ".join(where)
    total = connection.execute(
        f"SELECT COUNT(*) FROM daily_candidate_items i WHERE {predicate}", args
    ).fetchone()[0]
    rows = connection.execute(
        f"""SELECT first_limit_event_id item_id,run_id,first_limit_event_id,symbol,status,
                   candidate_id,error_type,last_error,started_at created_at,
                   updated_at
            FROM daily_candidate_items i WHERE {predicate}
            ORDER BY i.symbol,i.first_limit_event_id LIMIT ? OFFSET ?""",
        [*args, limit, offset],
    ).fetchall()
    return total, rows


def comparison_page(
    connection,
    *,
    run_id,
    symbol=None,
    change_type=None,
    grades=(),
    limit=100,
    offset=0,
):
    where = ["close.run_id=?"]
    args: list[object] = [run_id]
    if symbol:
        where.append("close.symbol=?")
        args.append(symbol)
    if change_type:
        where.append("close.change_type=?")
        args.append(change_type)
    _in_filter(where, args, "close.candidate_grade", grades)
    predicate = " AND ".join(where)
    total = connection.execute(
        f"SELECT COUNT(*) FROM daily_candidate_snapshots close WHERE {predicate}",
        args,
    ).fetchone()[0]
    rows = connection.execute(
        f"""SELECT close.first_limit_event_id,close.symbol,
                   close.preview_candidate_id,close.id close_candidate_id,
                   preview.lifecycle_status preview_lifecycle,
                   close.lifecycle_status close_lifecycle,
                   preview.candidate_grade preview_grade,
                   close.candidate_grade close_grade,close.change_type,
                   json_extract(close.primary_reasons_json,'$[0]')
                     change_reason_code,
                   (SELECT e.display_text FROM daily_candidate_evidence e
                    WHERE e.candidate_id=close.id
                    ORDER BY e.ordinal,e.rule_code LIMIT 1) change_display_text
            FROM daily_candidate_snapshots close
            LEFT JOIN daily_candidate_snapshots preview
              ON preview.id=close.preview_candidate_id
            WHERE {predicate}
            ORDER BY close.symbol,close.first_limit_event_id
            LIMIT ? OFFSET ?""",
        [*args, limit, offset],
    ).fetchall()
    return total, rows
