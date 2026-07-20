"""Collect multi-source sector statuses and persist real trend scores."""

from __future__ import annotations

import sys

from backend.collector.dividend_collector import get_akshare
from backend.collector.probe_sector_data import SourceResult, run_source
from backend.expectation_gap.database import connect, migrate
from backend.sector_radar.repository import (
    SectorScoreRecord,
    SourceStatusRecord,
    upsert_sector_scores,
    upsert_source_status,
)

SOURCE_ORDER = ("sw_l1", "sw_l2", "eastmoney")


def persist_results(connection, results: list[SourceResult]) -> int:
    saved = 0
    with connection:
        for result in results:
            status = result.status
            upsert_source_status(
                connection,
                SourceStatusRecord(
                    source=status.source,
                    status=status.status,
                    sector_count=status.sector_count,
                    successful_sector_count=status.successful_sector_count,
                    failed_sector_count=status.failed_sector_count,
                    last_error=status.last_error,
                    elapsed_seconds=status.elapsed_seconds,
                ),
            )
            records = [SectorScoreRecord(**trend.__dict__) for trend in result.trends]
            saved += upsert_sector_scores(connection, records)
    return saved


def collect_and_save() -> int:
    ak = get_akshare()
    results = [run_source(ak, source) for source in SOURCE_ORDER]
    connection = connect()
    try:
        migrate(connection)
        saved = persist_results(connection, results)
    finally:
        connection.close()
    print(f"已保存有效sector_scores: {saved}条")
    sw_l1 = results[0]
    if sw_l1.status.status == "unavailable":
        raise RuntimeError(f"sw_l1不可用: {sw_l1.status.last_error}")
    return saved


def main() -> int:
    try:
        collect_and_save()
    except Exception as exc:
        print(f"保存失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
