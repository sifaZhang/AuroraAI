"""Persistent SQLite ledger for the PR6.12 local one-click pipeline."""
from __future__ import annotations

import json
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dump(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def load(value, default=None):
    if value is None:
        return default
    return json.loads(value)


def create_or_reuse(connection, parameters, parameter_hash, steps):
    """Atomically create a natural-key job or return the existing one."""
    stamp = now()
    cursor = connection.execute(
        """INSERT OR IGNORE INTO first_limit_pipeline_jobs(
             trade_date,stage,as_of,data_cutoff,scope,universe_version,
             parameter_json,parameter_hash,status,progress_total,message,
             coverage_complete,created_at,heartbeat_at)
           VALUES(?,?,?,?,?,?,?,?,'pending',?,'等待执行',0,?,?)""",
        (
            parameters["trade_date"], parameters["stage"], parameters["as_of"],
            parameters["data_cutoff"], parameters["scope"],
            parameters["universe_version"], dump(parameters), parameter_hash,
            len(steps), stamp, stamp,
        ),
    )
    row = connection.execute(
        """SELECT * FROM first_limit_pipeline_jobs
           WHERE trade_date=? AND stage=? AND parameter_hash=?""",
        (parameters["trade_date"], parameters["stage"], parameter_hash),
    ).fetchone()
    if row is None:
        raise RuntimeError("unable to create pipeline job")
    if cursor.rowcount:
        connection.executemany(
            """INSERT INTO first_limit_pipeline_steps(
                 job_id,step_code,ordinal,status,input_summary_json,
                 output_summary_json)
               VALUES(?,?,?,'pending','{}','{}')""",
            [(row["id"], code, ordinal) for ordinal, code in enumerate(steps, 1)],
        )
    return row, bool(cursor.rowcount)


def job(connection, job_id):
    return connection.execute(
        "SELECT * FROM first_limit_pipeline_jobs WHERE id=?", (job_id,)
    ).fetchone()


def latest_job(connection, *, trade_date=None, stage=None):
    clauses, args = [], []
    if trade_date:
        clauses.append("trade_date=?")
        args.append(str(trade_date))
    if stage:
        clauses.append("stage=?")
        args.append(stage)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return connection.execute(
        f"SELECT * FROM first_limit_pipeline_jobs{where} ORDER BY id DESC LIMIT 1",
        args,
    ).fetchone()


def jobs(connection, *, trade_date=None, stage=None, statuses=(), limit=50, offset=0):
    clauses, args = [], []
    if trade_date:
        clauses.append("trade_date=?")
        args.append(str(trade_date))
    if stage:
        clauses.append("stage=?")
        args.append(stage)
    if statuses:
        clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
        args.extend(statuses)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    total = connection.execute(
        f"SELECT COUNT(*) FROM first_limit_pipeline_jobs{where}", args
    ).fetchone()[0]
    rows = connection.execute(
        f"""SELECT * FROM first_limit_pipeline_jobs{where}
            ORDER BY id DESC LIMIT ? OFFSET ?""",
        [*args, limit, offset],
    ).fetchall()
    return total, rows


def claim(connection, job_id):
    """Claim a pending/interrupted job. Caller must hold a short transaction."""
    stamp = now()
    cursor = connection.execute(
        """UPDATE first_limit_pipeline_jobs
           SET status='running',started_at=COALESCE(started_at,?),
               finished_at=NULL,heartbeat_at=?,error_code=NULL,error_message=NULL,
               message='正在启动'
           WHERE id=? AND status IN ('pending','interrupted')""",
        (stamp, stamp, job_id),
    )
    return cursor.rowcount == 1


def recover_stale(connection):
    stamp = now()
    cursor = connection.execute(
        """UPDATE first_limit_pipeline_jobs
           SET status='interrupted',finished_at=?,heartbeat_at=?,
               error_code='pipeline_interrupted',
               error_message='服务重启中断，等待安全续跑',
               message='任务已中断，可重试'
           WHERE status='running'""",
        (stamp, stamp),
    )
    connection.execute(
        """UPDATE first_limit_pipeline_steps
           SET status='interrupted',finished_at=?,
               error_code='pipeline_interrupted',
               error_message='服务重启中断'
           WHERE status='running'""",
        (stamp,),
    )
    return cursor.rowcount


def cancel(connection, job_id):
    """Cancel an active job without discarding its completed step outputs."""
    row = job(connection, job_id)
    if row is None:
        raise LookupError("pipeline job not found")
    if row["status"] in {"success", "partial", "failed", "cancelled"}:
        return row, False
    stamp = now()
    connection.execute(
        """UPDATE first_limit_pipeline_steps
           SET status='interrupted', finished_at=?, error_code='user_cancelled',
               error_message='cancelled by user'
           WHERE job_id=? AND status='running'""",
        (stamp, job_id),
    )
    connection.execute(
        """UPDATE first_limit_pipeline_jobs
           SET status='cancelled', finished_at=?, heartbeat_at=?,
               error_code='user_cancelled', error_message='cancelled by user',
               message='cancelled by user'
           WHERE id=?""",
        (stamp, stamp, job_id),
    )
    return job(connection, job_id), True


def prepare_retry(connection, job_id):
    row = job(connection, job_id)
    if row is None:
        raise LookupError("pipeline job not found")
    if row["status"] in {"pending", "running"}:
        return row, False
    stamp = now()
    connection.execute(
        """UPDATE first_limit_pipeline_jobs
           SET status='interrupted',finished_at=NULL,heartbeat_at=?,
               error_code=NULL,error_message=NULL,message='等待续跑',
               coverage_complete=0
           WHERE id=?""",
        (stamp, job_id),
    )
    connection.execute(
        """UPDATE first_limit_pipeline_steps SET status='pending',
                  started_at=NULL,finished_at=NULL,error_code=NULL,error_message=NULL
           WHERE job_id=? AND status NOT IN ('success','skipped')""",
        (job_id,),
    )
    return job(connection, job_id), True


def start_step(connection, job_id, code, *, input_summary=None, total=None):
    stamp = now()
    connection.execute(
        """UPDATE first_limit_pipeline_steps
           SET status='running',started_at=COALESCE(started_at,?),
               finished_at=NULL,progress_current=0,progress_total=?,
               input_summary_json=?,error_code=NULL,error_message=NULL
           WHERE job_id=? AND step_code=?""",
        (stamp, total, dump(input_summary or {}), job_id, code),
    )
    connection.execute(
        """UPDATE first_limit_pipeline_jobs
           SET current_step=?,heartbeat_at=?,message=? WHERE id=?""",
        (code, stamp, f"正在执行 {code}", job_id),
    )


def progress(connection, job_id, code, current, total=None, message=None):
    stamp = now()
    connection.execute(
        """UPDATE first_limit_pipeline_steps
           SET progress_current=?,progress_total=COALESCE(?,progress_total)
           WHERE job_id=? AND step_code=?""",
        (current, total, job_id, code),
    )
    connection.execute(
        """UPDATE first_limit_pipeline_jobs
           SET heartbeat_at=?,message=COALESCE(?,message) WHERE id=?""",
        (stamp, message, job_id),
    )


def finish_step(
    connection, job_id, code, status, *, output=None, error_code=None,
    error_message=None,
):
    stamp = now()
    connection.execute(
        """UPDATE first_limit_pipeline_steps
           SET status=?,finished_at=?,output_summary_json=?,
               error_code=?,error_message=?
           WHERE job_id=? AND step_code=?""",
        (
            status, stamp, dump(output or {}), error_code,
            (error_message or "")[:500] or None, job_id, code,
        ),
    )
    completed = connection.execute(
        """SELECT COUNT(*) FROM first_limit_pipeline_steps
           WHERE job_id=? AND status IN ('success','partial','failed','skipped')""",
        (job_id,),
    ).fetchone()[0]
    total = connection.execute(
        "SELECT COUNT(*) FROM first_limit_pipeline_steps WHERE job_id=?", (job_id,)
    ).fetchone()[0]
    percent = (completed * 100 / total) if total else None
    connection.execute(
        """UPDATE first_limit_pipeline_jobs
           SET progress_current=?,progress_total=?,progress_percent=?,
               heartbeat_at=? WHERE id=?""",
        (completed, total, percent, stamp, job_id),
    )


def steps(connection, job_id):
    return connection.execute(
        """SELECT * FROM first_limit_pipeline_steps WHERE job_id=?
           ORDER BY ordinal""",
        (job_id,),
    ).fetchall()


def save_coverage(
    connection, job_id, domain, *, required_start=None, required_end=None,
    expected_count=None, covered_count=0, missing_count=0, complete=False,
    details=None,
):
    ratio = (
        None if expected_count is None
        else 1 if expected_count == 0
        else covered_count / expected_count
    )
    connection.execute(
        """INSERT INTO first_limit_pipeline_coverage(
             job_id,domain,required_start,required_end,expected_count,
             covered_count,missing_count,coverage_ratio,complete,details_json)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(job_id,domain) DO UPDATE SET
             required_start=excluded.required_start,
             required_end=excluded.required_end,
             expected_count=excluded.expected_count,
             covered_count=excluded.covered_count,
             missing_count=excluded.missing_count,
             coverage_ratio=excluded.coverage_ratio,
             complete=excluded.complete,
             details_json=excluded.details_json""",
        (
            job_id, domain, required_start, required_end, expected_count,
            covered_count, missing_count, ratio, int(bool(complete)),
            dump(details or {}),
        ),
    )


def coverage(connection, job_id):
    return connection.execute(
        """SELECT * FROM first_limit_pipeline_coverage
           WHERE job_id=? ORDER BY domain""",
        (job_id,),
    ).fetchall()


def replace_universe(connection, job_id, records, source_cutoff):
    connection.execute(
        "DELETE FROM first_limit_pipeline_universe WHERE job_id=?", (job_id,)
    )
    connection.executemany(
        """INSERT INTO first_limit_pipeline_universe(
             job_id,symbol,eligible,exclusion_reason,source_cutoff,source_json)
           VALUES(?,?,?,?,?,?)""",
        [
            (
                job_id, record["symbol"], int(bool(record["eligible"])),
                record.get("exclusion_reason"), source_cutoff, dump(record),
            )
            for record in records
        ],
    )


def universe(connection, job_id, *, eligible_only=True):
    sql = "SELECT * FROM first_limit_pipeline_universe WHERE job_id=?"
    if eligible_only:
        sql += " AND eligible=1"
    return connection.execute(sql + " ORDER BY symbol", (job_id,)).fetchall()


def record_failure(
    connection, job_id, step_code, error_code, error_message, *,
    symbol=None, trade_date=None,
):
    connection.execute(
        """INSERT OR IGNORE INTO first_limit_pipeline_failures(
             job_id,step_code,symbol,trade_date,error_code,error_message,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            job_id, step_code, symbol, trade_date, error_code,
            (error_message or "")[:500], now(),
        ),
    )


def failures(connection, job_id, *, limit=100, offset=0):
    total = connection.execute(
        "SELECT COUNT(*) FROM first_limit_pipeline_failures WHERE job_id=?",
        (job_id,),
    ).fetchone()[0]
    rows = connection.execute(
        """SELECT * FROM first_limit_pipeline_failures WHERE job_id=?
           ORDER BY id LIMIT ? OFFSET ?""",
        (job_id, limit, offset),
    ).fetchall()
    return total, rows


def finish_job(
    connection, job_id, status, *, candidate_run_id=None,
    coverage_complete=False, error_code=None, error_message=None,
):
    stamp = now()
    connection.execute(
        """UPDATE first_limit_pipeline_jobs
           SET status=?,candidate_run_id=COALESCE(?,candidate_run_id),
               coverage_complete=?,finished_at=?,heartbeat_at=?,
               progress_percent=CASE WHEN ? IN ('success','partial') THEN 100
                                     ELSE progress_percent END,
               message=?,error_code=?,error_message=?
           WHERE id=?""",
        (
            status, candidate_run_id, int(bool(coverage_complete)), stamp, stamp,
            status,
            {
                "success": "完整筛选完成",
                "partial": "部分完成，请查看覆盖报告",
                "failed": "执行失败",
            }.get(status, status),
            error_code, (error_message or "")[:500] or None, job_id,
        ),
    )
