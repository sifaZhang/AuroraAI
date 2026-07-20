from __future__ import annotations

import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "aurora.db"
MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "001_expectation_gap.sql"
QUALITY_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "002_expectation_quality.sql"
REFRESH_JOBS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "003_refresh_jobs.sql"
SECTOR_SCORES_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "004_sector_scores.sql"


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
