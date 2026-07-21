from __future__ import annotations

import os
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "aurora.db"
MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "001_expectation_gap.sql"
QUALITY_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "002_expectation_quality.sql"
REFRESH_JOBS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "003_refresh_jobs.sql"
SECTOR_SCORES_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "004_sector_scores.sql"
DATA_SOURCE_HEALTH_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "005_data_source_health.sql"
MARKET_PULSE_REFRESH_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "006_market_pulse_refresh.sql"
SECTOR_RELATIVE_STRENGTH_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "007_sector_relative_strength.sql"
A_SHARE_DAILY_BARS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "008_a_share_daily_bars.sql"
SECTOR_HISTORY_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "009_sector_history.sql"
SECTOR_BREADTH_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "010_sector_breadth_scores.sql"


def database_path() -> Path:
    url = os.getenv("EXPECTATION_DB_URL", "sqlite:///./data/aurora.db")
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("Phase A only supports sqlite:/// EXPECTATION_DB_URL values")
    raw_path = Path(url[len(prefix) :])
    return raw_path if raw_path.is_absolute() else PROJECT_ROOT / raw_path


def connect(path: Path | None = None) -> sqlite3.Connection:
    resolved = path or database_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(QUALITY_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(REFRESH_JOBS_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(SECTOR_SCORES_MIGRATION_PATH.read_text(encoding="utf-8"))
    _migrate_sector_source_status(connection)
    connection.executescript(DATA_SOURCE_HEALTH_MIGRATION_PATH.read_text(encoding="utf-8"))
    _migrate_refresh_jobs_for_market_pulse(connection)
    connection.executescript(MARKET_PULSE_REFRESH_MIGRATION_PATH.read_text(encoding="utf-8"))
    _migrate_sector_relative_strength(connection)
    connection.executescript(SECTOR_RELATIVE_STRENGTH_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(A_SHARE_DAILY_BARS_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(SECTOR_HISTORY_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(SECTOR_BREADTH_MIGRATION_PATH.read_text(encoding="utf-8"))
    existing = {row[1] for row in connection.execute("PRAGMA table_info(stock_expectations)")}
    additions = {
        "price_source": "TEXT",
        "morningstar_source": "TEXT",
        "morningstar_imported_at": "TEXT",
        "morningstar_gap_pct": "NUMERIC",
        "analyst_source": "TEXT",
        "analyst_report_count": "INTEGER",
        "analyst_window_days": "INTEGER",
        "analyst_gap_pct": "NUMERIC",
        "price_check_status": "TEXT",
        "morningstar_check_status": "TEXT",
        "morningstar_next_check_at": "TEXT",
        "analyst_check_status": "TEXT",
        "analyst_next_check_at": "TEXT",
    }
    for column, sql_type in additions.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE stock_expectations ADD COLUMN {column} {sql_type}")
    stock_columns = {row[1] for row in connection.execute("PRAGMA table_info(stocks)")}
    if "is_reit" not in stock_columns:
        connection.execute("ALTER TABLE stocks ADD COLUMN is_reit INTEGER NOT NULL DEFAULT 0")
    run_columns = {row[1] for row in connection.execute("PRAGMA table_info(refresh_runs)")}
    if "no_data_count" not in run_columns:
        connection.execute("ALTER TABLE refresh_runs ADD COLUMN no_data_count INTEGER NOT NULL DEFAULT 0")
    quality_columns = {row[1] for row in connection.execute("PRAGMA table_info(stock_expectation_quality)")}
    for column in ("morningstar_quality_details", "analyst_quality_details"):
        if column not in quality_columns:
            connection.execute(f"ALTER TABLE stock_expectation_quality ADD COLUMN {column} TEXT NOT NULL DEFAULT '{{}}'")
    connection.commit()


def _migrate_sector_source_status(connection: sqlite3.Connection) -> None:
    required = {
        "display_name", "last_failure_at", "last_error_type", "last_error_message",
        "latency_ms", "consecutive_failures", "total_successes", "total_failures", "metadata_json",
    }
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sector_source_status)")}
    table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sector_source_status'"
    ).fetchone()
    table_sql = table_row[0] if table_row else ""
    if required.issubset(columns) and "healthy" in table_sql and "unknown" in table_sql and "benchmark_csi300" in table_sql:
        return

    existing = [dict(row) for row in connection.execute("SELECT * FROM sector_source_status")]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    display_names = {"sw_l1": "申万一级行业", "sw_l2": "申万二级行业", "eastmoney": "东方财富行业", "benchmark_csi300": "沪深300基准"}
    connection.execute("ALTER TABLE sector_source_status RENAME TO sector_source_status_legacy")
    connection.execute(
        """CREATE TABLE sector_source_status (
            source TEXT PRIMARY KEY CHECK(source IN ('sw_l1','sw_l2','eastmoney','benchmark_csi300')),
            display_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('healthy','degraded','unavailable','unknown')),
            sector_count INTEGER NOT NULL DEFAULT 0,
            successful_sector_count INTEGER NOT NULL DEFAULT 0,
            failed_sector_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            last_success_at TEXT,
            last_failure_at TEXT,
            last_error TEXT,
            last_error_type TEXT,
            last_error_message TEXT,
            latency_ms NUMERIC,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            total_successes INTEGER NOT NULL DEFAULT 0,
            total_failures INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            elapsed_seconds NUMERIC NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )"""
    )
    status_map = {"available": "healthy", "partial": "degraded", "unavailable": "unavailable"}
    for row in existing:
        status = status_map.get(row.get("status"), row.get("status", "unknown"))
        error = row.get("last_error")
        connection.execute(
            """INSERT INTO sector_source_status(
                source,display_name,status,sector_count,successful_sector_count,failed_sector_count,
                last_attempt_at,last_success_at,last_failure_at,last_error,last_error_type,last_error_message,
                latency_ms,consecutive_failures,total_successes,total_failures,metadata_json,elapsed_seconds,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["source"], display_names.get(row["source"], row["source"]), status,
                row.get("sector_count", 0), row.get("successful_sector_count", 0), row.get("failed_sector_count", 0),
                row.get("last_attempt_at"), row.get("last_success_at"),
                row.get("last_attempt_at") if status == "unavailable" else None,
                error, "LegacySourceError" if error else None, error,
                float(row.get("elapsed_seconds", 0) or 0) * 1000,
                1 if status == "unavailable" else 0,
                1 if row.get("last_success_at") else 0, 1 if status in {"degraded", "unavailable"} else 0,
                json.dumps({}, ensure_ascii=False), row.get("elapsed_seconds", 0), row.get("updated_at") or now,
            ),
        )
    connection.execute("DROP TABLE sector_source_status_legacy")


def _migrate_sector_relative_strength(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sector_scores)")}
    additions = {
        "relative_strength_score": "INTEGER CHECK(relative_strength_score BETWEEN 0 AND 15)",
        "benchmark_code": "TEXT",
        "benchmark_trade_date": "TEXT",
        "sector_return_5d": "NUMERIC",
        "benchmark_return_5d": "NUMERIC",
        "excess_return_5d": "NUMERIC",
        "sector_return_10d": "NUMERIC",
        "benchmark_return_10d": "NUMERIC",
        "excess_return_10d": "NUMERIC",
        "sector_return_20d": "NUMERIC",
        "benchmark_return_20d": "NUMERIC",
        "excess_return_20d": "NUMERIC",
        "relative_strength_updated_at": "TEXT",
        "capital_flow_score": "INTEGER CHECK(capital_flow_score BETWEEN 0 AND 15)",
        "composite_score": "INTEGER CHECK(composite_score BETWEEN 0 AND 100)",
        "score_status": "TEXT CHECK(score_status IN ('complete','partial','unavailable'))",
        "missing_components": "TEXT",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE sector_scores ADD COLUMN {column} {definition}")


def _migrate_refresh_jobs_for_market_pulse(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(refresh_jobs)")}
    table_row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='refresh_jobs'").fetchone()
    table_sql = table_row[0] if table_row else ""
    if "source" in columns and "refresh_market_pulse" in table_sql:
        return
    rows = [dict(row) for row in connection.execute("SELECT * FROM refresh_jobs")]
    connection.execute("ALTER TABLE refresh_jobs RENAME TO refresh_jobs_legacy")
    connection.execute(
        """CREATE TABLE refresh_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL CHECK(job_type IN ('refresh_a_share','refresh_hk_prices','refresh_hk_ratings','refresh_market_pulse')),
            source TEXT CHECK(source IS NULL OR source IN ('sw_l1','sw_l2','eastmoney','all')),
            status TEXT NOT NULL CHECK(status IN ('pending','running','success','partial','failed')),
            total INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0, no_data_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0, skipped_count INTEGER NOT NULL DEFAULT 0,
            progress_pct NUMERIC NOT NULL DEFAULT 0, current_code TEXT, message TEXT,
            error_summary TEXT, started_at TEXT, finished_at TEXT, created_at TEXT NOT NULL
        )"""
    )
    fields = (
        "id", "job_type", "status", "total", "processed", "success_count", "no_data_count",
        "failure_count", "skipped_count", "progress_pct", "current_code", "message",
        "error_summary", "started_at", "finished_at", "created_at",
    )
    placeholders = ",".join("?" for _ in fields)
    for row in rows:
        connection.execute(
            f"INSERT INTO refresh_jobs({','.join(fields)}) VALUES({placeholders})",
            tuple(row.get(field) for field in fields),
        )
    connection.execute("DROP TABLE refresh_jobs_legacy")
