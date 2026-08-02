"""CLI for the same persistent PR6.12 service used by the API and page."""
from __future__ import annotations

import argparse
import json

from backend.expectation_gap.database import connect, migrate

from . import pipeline_repository as repo
from . import pipeline_service as service


def parser():
    value = argparse.ArgumentParser(
        description="Run the first-limit one-click preparation pipeline"
    )
    value.add_argument("--trade-date")
    value.add_argument("--stage", choices=("tail_preview", "close_confirmed"))
    value.add_argument("--as-of")
    value.add_argument("--data-cutoff")
    value.add_argument("--resume-job-id", type=int)
    value.add_argument("--symbols", help="controlled partial-scope symbols")
    value.add_argument("--report", choices=("json", "markdown"), default="json")
    value.add_argument("--wait", action="store_true")
    return value


def _markdown(connection, row):
    steps = repo.steps(connection, row["id"])
    coverage = repo.coverage(connection, row["id"])
    lines = [
        f"# First-limit pipeline job {row['id']}", "",
        f"- Status: {row['status']}",
        f"- Trade date: {row['trade_date']}",
        f"- Stage: {row['stage']}",
        f"- Scope: {row['scope']}",
        f"- Coverage complete: {bool(row['coverage_complete'])}", "",
        "## Steps", "",
    ]
    lines.extend(
        f"- {step['step_code']}: {step['status']}"
        f" ({step['duration_seconds']:.3f}s)"
        if step["duration_seconds"] is not None
        else f"- {step['step_code']}: {step['status']}"
        for step in steps
    )
    lines.extend(["", "## Coverage", ""])
    lines.extend(
        f"- {item['domain']}: {item['covered_count']}/"
        f"{item['expected_count'] if item['expected_count'] is not None else 'unknown'}"
        for item in coverage
    )
    return "\n".join(lines)


def main(argv=None):
    args = parser().parse_args(argv)
    connection = connect()
    try:
        migrate(connection)
        if args.resume_job_id:
            if any((
                args.trade_date, args.stage, args.as_of, args.data_cutoff,
                args.symbols,
            )):
                raise ValueError(
                    "--resume-job-id cannot be combined with new job parameters"
                )
            with connection:
                row, _changed = repo.prepare_retry(
                    connection, args.resume_job_id
                )
            job_id = row["id"]
        else:
            if not args.trade_date or not args.stage:
                raise ValueError("--trade-date and --stage are required")
            symbols = (
                [item.strip() for item in args.symbols.split(",") if item.strip()]
                if args.symbols else None
            )
            created = service.create_job(
                connection, trade_date=args.trade_date, stage=args.stage,
                as_of=args.as_of, data_cutoff=args.data_cutoff, symbols=symbols,
            )
            job_id = created["job_id"]
        row = (
            service.execute_job(connection, job_id)
            if args.wait else repo.job(connection, job_id)
        )
        if not args.wait:
            service.start_background(job_id)
        if args.report == "markdown":
            print(_markdown(connection, row))
        else:
            print(json.dumps(
                service.serialize_job(row), ensure_ascii=False, indent=2
            ))
        if not args.wait:
            return 0
        return 0 if row["status"] == "success" else 1 if row["status"] == "partial" else 2
    except (ValueError, LookupError, service.PipelineError) as exc:
        print(f"ERROR: {exc}")
        return 2
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
