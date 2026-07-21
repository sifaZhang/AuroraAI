"""SQLite access for Market Breadth inputs and versioned results."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from backend.sector_radar.breadth import MarketBreadthResult


def get_trend_score(connection: sqlite3.Connection, classification_system: str,
                    sector_code: str, trade_date: str) -> float | None:
    source = {"sw_level1": "sw_l1"}.get(classification_system)
    if source is None:
        return None
    row = connection.execute(
        """SELECT trend_score FROM sector_scores
           WHERE source=? AND sector_code=? AND trade_date=?""",
        (source, sector_code, trade_date),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def breadth_result_exists(connection: sqlite3.Connection, classification_system: str,
                          sector_code: str, trade_date: str, calculation_version: str) -> bool:
    return connection.execute(
        """SELECT 1 FROM sector_breadth_scores WHERE classification_system=? AND sector_code=?
           AND trade_date=? AND calculation_version=?""",
        (classification_system, sector_code, trade_date, calculation_version),
    ).fetchone() is not None


def upsert_breadth_result(connection: sqlite3.Connection, result: MarketBreadthResult) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    metric = result.metrics
    component = result.components
    values = (
        result.classification_system, result.sector_code, result.trade_date,
        result.membership_snapshot_date,
        metric["above_ma5"].ratio, metric["above_ma5"].numerator, metric["above_ma5"].denominator,
        metric["above_ma10"].ratio, metric["above_ma10"].numerator, metric["above_ma10"].denominator,
        metric["above_ma20"].ratio, metric["above_ma20"].numerator, metric["above_ma20"].denominator,
        metric["advancing"].ratio, metric["advancing"].numerator, metric["advancing"].denominator,
        metric["new_high_20"].ratio, metric["new_high_20"].numerator, metric["new_high_20"].denominator,
        metric["volume_expansion"].ratio, metric["volume_expansion"].numerator,
        metric["volume_expansion"].denominator,
        result.total_members, result.valid_members, result.coverage_ratio,
        json.dumps(result.excluded_members, ensure_ascii=False, sort_keys=True),
        component["above_ma20"].score, component["advancing"].score,
        component["new_high_20"].score, component["volume_expansion"].score,
        result.breadth_score, result.trend_score, result.total_score, result.status,
        json.dumps(result.quality_warnings, ensure_ascii=False), int(result.is_approximate),
        result.lookahead_warning, result.calculation_version, now, now,
    )
    with connection:
        connection.execute(
            """INSERT INTO sector_breadth_scores(
                classification_system,sector_code,trade_date,membership_snapshot_date,
                above_ma5_ratio,above_ma5_numerator,above_ma5_valid_count,
                above_ma10_ratio,above_ma10_numerator,above_ma10_valid_count,
                above_ma20_ratio,above_ma20_numerator,above_ma20_valid_count,
                advancing_ratio,advancing_numerator,advancing_valid_count,
                new_high_20_ratio,new_high_20_numerator,new_high_20_valid_count,
                volume_expansion_ratio,volume_expansion_numerator,volume_expansion_valid_count,
                total_members,valid_members,coverage_ratio,excluded_members,
                ma20_score,advancing_score,new_high_20_score,volume_expansion_score,
                breadth_score,trend_score,total_score,status,quality_warnings,is_approximate,
                lookahead_warning,calculation_version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(classification_system,sector_code,trade_date,calculation_version) DO UPDATE SET
                membership_snapshot_date=excluded.membership_snapshot_date,
                above_ma5_ratio=excluded.above_ma5_ratio,above_ma5_numerator=excluded.above_ma5_numerator,
                above_ma5_valid_count=excluded.above_ma5_valid_count,
                above_ma10_ratio=excluded.above_ma10_ratio,above_ma10_numerator=excluded.above_ma10_numerator,
                above_ma10_valid_count=excluded.above_ma10_valid_count,
                above_ma20_ratio=excluded.above_ma20_ratio,above_ma20_numerator=excluded.above_ma20_numerator,
                above_ma20_valid_count=excluded.above_ma20_valid_count,
                advancing_ratio=excluded.advancing_ratio,advancing_numerator=excluded.advancing_numerator,
                advancing_valid_count=excluded.advancing_valid_count,
                new_high_20_ratio=excluded.new_high_20_ratio,new_high_20_numerator=excluded.new_high_20_numerator,
                new_high_20_valid_count=excluded.new_high_20_valid_count,
                volume_expansion_ratio=excluded.volume_expansion_ratio,
                volume_expansion_numerator=excluded.volume_expansion_numerator,
                volume_expansion_valid_count=excluded.volume_expansion_valid_count,
                total_members=excluded.total_members,valid_members=excluded.valid_members,
                coverage_ratio=excluded.coverage_ratio,excluded_members=excluded.excluded_members,
                ma20_score=excluded.ma20_score,advancing_score=excluded.advancing_score,
                new_high_20_score=excluded.new_high_20_score,
                volume_expansion_score=excluded.volume_expansion_score,
                breadth_score=excluded.breadth_score,trend_score=excluded.trend_score,
                total_score=excluded.total_score,status=excluded.status,
                quality_warnings=excluded.quality_warnings,is_approximate=excluded.is_approximate,
                lookahead_warning=excluded.lookahead_warning,updated_at=excluded.updated_at""",
            values,
        )
    return 1
