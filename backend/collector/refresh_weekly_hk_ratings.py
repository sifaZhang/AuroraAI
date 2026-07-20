from __future__ import annotations

from backend.expectation_gap.database import connect, migrate
from backend.expectation_gap.refresh_jobs import JobConflictError, create_job, get_job, run_job


def main() -> int:
    connection = connect(); migrate(connection)
    try:
        try:
            job_id = create_job(connection, "refresh_hk_ratings")
        except JobConflictError as exc:
            print(f"安全退出：{exc}")
            return 2
        print(f"已创建每周港股评级刷新任务：{job_id}")
    finally:
        connection.close()
    run_job(job_id)
    connection = connect()
    try:
        job = get_job(connection, job_id)
    finally:
        connection.close()
    print(job)
    return 0 if job and job["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
