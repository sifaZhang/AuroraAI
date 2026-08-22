from __future__ import annotations

import os
import json
import sqlite3
import threading
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
FIRST_LIMIT_STRATEGY_CONTRACT_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "011_first_limit_strategy_contract.sql"
FIRST_LIMIT_STRATEGY_SYNC_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "012_first_limit_data_sync.sql"
FIRST_LIMIT_EVENTS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "013_first_limit_events.sql"
FIRST_LIMIT_DETECTION_RUNS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "014_first_limit_detection_runs.sql"
FIRST_LIMIT_DETECTION_ITEM_RESULTS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "015_first_limit_detection_item_results.sql"
FIRST_LIMIT_QUALITY_SCORES_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "016_first_limit_quality_scores.sql"
FIRST_LIMIT_PULLBACK_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "017_first_limit_pullback.sql"
FIRST_LIMIT_CONTEXT_SCORING_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "018_first_limit_context_scoring.sql"
FIRST_LIMIT_DAILY_BACKTEST_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "019_first_limit_daily_backtest.sql"
FIRST_LIMIT_MINUTE_REVIEW_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "020_first_limit_minute_review.sql"
FIRST_LIMIT_DAILY_CANDIDATES_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "021_first_limit_daily_candidates.sql"
FIRST_LIMIT_PIPELINE_JOBS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "022_first_limit_pipeline_jobs.sql"
CURRENT_SW_INDUSTRY_SNAPSHOT_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "023_current_sw_industry_snapshot.sql"
INDUSTRY_DAILY_SNAPSHOTS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "024_industry_daily_snapshots.sql"
INDUSTRY_DAILY_SCORES_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "025_industry_daily_scores.sql"
FIRST_LIMIT_INDUSTRY_CONTEXT_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "026_first_limit_industry_context.sql"
FIRST_LIMIT_CANDIDATE_SCORING_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "027_first_limit_candidate_scoring.sql"
FIRST_LIMIT_CLOSE_CONFIRMATION_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "028_first_limit_close_confirmation.sql"
DIVIDEND_STABLE_UNIVERSE_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "029_dividend_stable_universe.sql"
DIVIDEND_YIELD_SNAPSHOTS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "030_dividend_yield_snapshots.sql"
DIVIDEND_HIGH_WATCH_SUBTYPE_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "031_dividend_high_watch_subtype.sql"
DIVIDEND_CURRENT_BASIS_DPS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "032_dividend_current_basis_dps.sql"
DIVIDEND_UNIVERSE_POSITION_LEVELS_MIGRATION_PATH = PROJECT_ROOT / "database" / "migrations" / "033_dividend_universe_position_levels.sql"
MIGRATION_LOCK = threading.RLock()
WRITE_JOB_LOCK = threading.RLock()
SQLITE_TIMEOUT_SECONDS = 30


class DatabaseWriteBusyError(RuntimeError):
    """Raised when another background job owns the database write lane."""


def acquire_write_job(*, blocking: bool) -> bool:
    return WRITE_JOB_LOCK.acquire(blocking=blocking)


def release_write_job() -> None:
    WRITE_JOB_LOCK.release()


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
    # FastAPI may enter and finalize a synchronous generator dependency on
    # different worker threads. Each request still owns its own connection,
    # but SQLite must allow that connection to follow the request between
    # those worker threads.
    connection = sqlite3.connect(
        resolved, timeout=SQLITE_TIMEOUT_SECONDS, check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
    return connection


def connect_readonly(path: Path | None = None) -> sqlite3.Connection:
    """Open the configured SQLite database without creating or mutating it."""
    resolved = path or database_path()
    if not resolved.exists():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=ro", uri=True,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _migrate_unlocked(connection: sqlite3.Connection) -> None:
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
    connection.executescript(FIRST_LIMIT_STRATEGY_CONTRACT_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(FIRST_LIMIT_STRATEGY_SYNC_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(FIRST_LIMIT_EVENTS_MIGRATION_PATH.read_text(encoding="utf-8"))
    run_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='first_limit_sync_runs'").fetchone()[0]
    if "'detect'" not in run_sql:
        connection.executescript(FIRST_LIMIT_DETECTION_RUNS_MIGRATION_PATH.read_text(encoding="utf-8"))
    sync_item_columns = {row[1] for row in connection.execute("PRAGMA table_info(first_limit_sync_items)")}
    if "result_json" not in sync_item_columns:
        connection.executescript(FIRST_LIMIT_DETECTION_ITEM_RESULTS_MIGRATION_PATH.read_text(encoding="utf-8"))
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='first_limit_quality_scores'").fetchone() is None:
        connection.executescript(FIRST_LIMIT_QUALITY_SCORES_MIGRATION_PATH.read_text(encoding="utf-8"))
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='first_limit_pullback_observations'").fetchone() is None:
        connection.executescript(FIRST_LIMIT_PULLBACK_MIGRATION_PATH.read_text(encoding="utf-8"))
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='first_limit_context_scores'").fetchone() is None:
        connection.executescript(FIRST_LIMIT_CONTEXT_SCORING_MIGRATION_PATH.read_text(encoding="utf-8"))
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backtest_runs'").fetchone() is None:
        connection.executescript(FIRST_LIMIT_DAILY_BACKTEST_MIGRATION_PATH.read_text(encoding="utf-8"))
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='minute_review_runs'").fetchone() is None:
        connection.executescript(FIRST_LIMIT_MINUTE_REVIEW_MIGRATION_PATH.read_text(encoding="utf-8"))
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_candidate_runs'").fetchone() is None:
        connection.executescript(FIRST_LIMIT_DAILY_CANDIDATES_MIGRATION_PATH.read_text(encoding="utf-8"))
    daily_candidate_run_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(daily_candidate_runs)")
    }
    if "detection_complete" not in daily_candidate_run_columns:
        connection.execute(
            """ALTER TABLE daily_candidate_runs
               ADD COLUMN detection_complete INTEGER NOT NULL DEFAULT 0
               CHECK(detection_complete IN (0,1))"""
        )
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='first_limit_pipeline_jobs'"
    ).fetchone() is None:
        connection.executescript(
            FIRST_LIMIT_PIPELINE_JOBS_MIGRATION_PATH.read_text(encoding="utf-8")
        )
    connection.executescript(CURRENT_SW_INDUSTRY_SNAPSHOT_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(INDUSTRY_DAILY_SNAPSHOTS_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(INDUSTRY_DAILY_SCORES_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(DIVIDEND_STABLE_UNIVERSE_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(DIVIDEND_YIELD_SNAPSHOTS_MIGRATION_PATH.read_text(encoding="utf-8"))
    universe_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='dividend_stable_universe'"
    ).fetchone()
    if universe_sql_row is not None and "high_dividend_watch" not in universe_sql_row[0]:
        connection.executescript(DIVIDEND_HIGH_WATCH_SUBTYPE_MIGRATION_PATH.read_text(encoding="utf-8"))
    annual_dps_columns = {row[1] for row in connection.execute("PRAGMA table_info(annual_cash_dividend_summaries)")}
    if "current_basis_dps" not in annual_dps_columns:
        connection.executescript(DIVIDEND_CURRENT_BASIS_DPS_MIGRATION_PATH.read_text(encoding="utf-8"))
    universe_columns = {row[1] for row in connection.execute("PRAGMA table_info(dividend_stable_universe)")}
    if "grade" not in universe_columns:
        connection.executescript(DIVIDEND_UNIVERSE_POSITION_LEVELS_MIGRATION_PATH.read_text(encoding="utf-8"))
    candidate_columns = {row[1] for row in connection.execute("PRAGMA table_info(daily_candidate_snapshots)")}
    if "effective_industry_code" not in candidate_columns:
        connection.executescript(FIRST_LIMIT_INDUSTRY_CONTEXT_MIGRATION_PATH.read_text(encoding="utf-8"))
    candidate_columns = {row[1] for row in connection.execute("PRAGMA table_info(daily_candidate_snapshots)")}
    scoring_columns = {
        "effective_score": "REAL", "effective_rank": "INTEGER",
        "capital_activity_score": "REAL", "leader_score": "REAL",
        "industry_trend_score": "REAL", "industry_environment_score": "REAL",
        "buy_recommendation": "TEXT", "scoring_version": "TEXT",
    }
    for column, sql_type in scoring_columns.items():
        if column not in candidate_columns:
            connection.execute(f"ALTER TABLE daily_candidate_snapshots ADD COLUMN {column} {sql_type}")
    run_columns = {row[1] for row in connection.execute("PRAGMA table_info(daily_candidate_runs)")}
    if "summary_json" not in run_columns:
        connection.execute("ALTER TABLE daily_candidate_runs ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}'")
    candidate_columns = {row[1] for row in connection.execute("PRAGMA table_info(daily_candidate_snapshots)")}
    confirmation_columns = {
        "intraday_total_score": "REAL", "intraday_candidate_grade": "TEXT",
        "official_industry_score": "REAL", "official_industry_rank": "INTEGER",
        "final_total_score": "REAL", "final_candidate_grade": "TEXT",
        "final_buy_recommendation": "TEXT", "confirmation_status": "TEXT NOT NULL DEFAULT 'intraday'",
        "confirmation_change_type": "TEXT", "confirmed_at": "TEXT",
    }
    for column, sql_type in confirmation_columns.items():
        if column not in candidate_columns:
            connection.execute(f"ALTER TABLE daily_candidate_snapshots ADD COLUMN {column} {sql_type}")
    backtest_columns = {row[1] for row in connection.execute("PRAGMA table_info(backtest_signals)")}
    for column in ("intraday_grade", "final_grade", "intraday_to_final_change", "next_day_result"):
        if column not in backtest_columns:
            connection.execute(f"ALTER TABLE backtest_signals ADD COLUMN {column} TEXT")
    security_columns = {row[1] for row in connection.execute("PRAGMA table_info(a_share_security_master)")}
    if "is_active" not in security_columns:
        connection.execute("ALTER TABLE a_share_security_master ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1))")
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


def migrate(connection: sqlite3.Connection) -> None:
    """Apply schema migrations once at a time within the API process.

    Page load requests and background jobs can arrive concurrently.  Several
    legacy migrations include DDL, which takes SQLite's exclusive write lock;
    serialising them prevents otherwise harmless concurrent requests from
    failing with ``database is locked``.
    """
    with MIGRATION_LOCK:
        _migrate_unlocked(connection)


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
    required_sources = ("tushare", "futu_opend", "sw_l1", "sw_l2", "sw_l3", "eastmoney", "benchmark_csi300")
    if (required.issubset(columns) and "healthy" in table_sql and "unknown" in table_sql
            and all(source in table_sql for source in required_sources)):
        return

    existing = [dict(row) for row in connection.execute("SELECT * FROM sector_source_status")]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    display_names = {
        "tushare": "Tushare", "futu_opend": "富途 OpenD",
        "sw_l1": "申万一级行业", "sw_l2": "申万二级行业", "sw_l3": "申万三级行业",
        "eastmoney": "东方财富行业行情", "benchmark_csi300": "沪深300基准",
    }
    connection.execute("ALTER TABLE sector_source_status RENAME TO sector_source_status_legacy")
    connection.execute(
        """CREATE TABLE sector_source_status (
            source TEXT PRIMARY KEY CHECK(source IN (
                'tushare','futu_opend','sw_l1','sw_l2','sw_l3','eastmoney','benchmark_csi300'
            )),
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
    if "source" in columns and "refresh_market_pulse" in table_sql and "HK.[0-9]*" in table_sql and "A.*" in table_sql:
        return
    rows = [dict(row) for row in connection.execute("SELECT * FROM refresh_jobs")]
    connection.execute("ALTER TABLE refresh_jobs RENAME TO refresh_jobs_legacy")
    connection.execute(
        """CREATE TABLE refresh_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL CHECK(job_type IN ('refresh_a_share','refresh_hk_prices','refresh_hk_ratings','refresh_market_pulse')),
            source TEXT CHECK(source IS NULL OR source IN ('sw_l1','sw_l2','eastmoney','all') OR source GLOB 'HK.[0-9]*' OR source GLOB 'A.*'),
            status TEXT NOT NULL CHECK(status IN ('pending','running','success','partial','failed')),
            total INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0, no_data_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0, skipped_count INTEGER NOT NULL DEFAULT 0,
            progress_pct NUMERIC NOT NULL DEFAULT 0, current_code TEXT, message TEXT,
            error_summary TEXT, started_at TEXT, finished_at TEXT, created_at TEXT NOT NULL
        )"""
    )
    fields = (
        "id", "job_type", "source", "status", "total", "processed", "success_count", "no_data_count",
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
