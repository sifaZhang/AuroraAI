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
    if required.issubset(columns) and "healthy" in table_sql and "unknown" in table_sql:
        return

    existing = [dict(row) for row in connection.execute("SELECT * FROM sector_source_status")]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    display_names = {"sw_l1": "申万一级行业", "sw_l2": "申万二级行业", "eastmoney": "东方财富行业"}
    connection.execute("ALTER TABLE sector_source_status RENAME TO sector_source_status_legacy")
    connection.execute(
        """CREATE TABLE sector_source_status (
            source TEXT PRIMARY KEY CHECK(source IN ('sw_l1','sw_l2','eastmoney')),
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
