-- PR6.2 keeps authoritative strategy metadata separate from shared OHLCV bars.
CREATE TABLE IF NOT EXISTS first_limit_daily_metadata (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    pre_close NUMERIC,
    source_upper_limit NUMERIC,
    source_lower_limit NUMERIC,
    data_source TEXT NOT NULL CHECK(data_source IN ('SINA','GM','CALCULATED','MANUAL','UNKNOWN')),
    quality_flags TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_first_limit_daily_metadata_date
ON first_limit_daily_metadata(trade_date, symbol);

CREATE TABLE IF NOT EXISTS first_limit_minute_bars (
    symbol TEXT NOT NULL,
    bar_time TEXT NOT NULL,
    timeframe TEXT NOT NULL CHECK(timeframe = '1m'),
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC,
    amount NUMERIC,
    data_source TEXT NOT NULL CHECK(data_source IN ('SINA','GM','CALCULATED','MANUAL','UNKNOWN')),
    adjustment TEXT NOT NULL CHECK(adjustment = 'none'),
    quality_flags TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(symbol, bar_time, timeframe)
);
CREATE INDEX IF NOT EXISTS idx_first_limit_minute_bars_lookup
ON first_limit_minute_bars(symbol, timeframe, bar_time);

CREATE TABLE IF NOT EXISTS first_limit_sync_runs (
    run_id TEXT PRIMARY KEY,
    sync_type TEXT NOT NULL CHECK(sync_type IN ('calendar','securities','statuses','daily','minute','audit')),
    parameters_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','success','partial','failed','interrupted')),
    data_source TEXT NOT NULL CHECK(data_source IN ('SINA','GM','CALCULATED','MANUAL','UNKNOWN')),
    planned_count INTEGER NOT NULL DEFAULT 0 CHECK(planned_count >= 0),
    success_count INTEGER NOT NULL DEFAULT 0 CHECK(success_count >= 0),
    empty_count INTEGER NOT NULL DEFAULT 0 CHECK(empty_count >= 0),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count >= 0),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
    inserted_rows INTEGER NOT NULL DEFAULT 0 CHECK(inserted_rows >= 0),
    updated_rows INTEGER NOT NULL DEFAULT 0 CHECK(updated_rows >= 0),
    unchanged_rows INTEGER NOT NULL DEFAULT 0 CHECK(unchanged_rows >= 0),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    last_error TEXT,
    is_dry_run INTEGER NOT NULL CHECK(is_dry_run IN (0,1)),
    sync_version TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS first_limit_sync_items (
    run_id TEXT NOT NULL REFERENCES first_limit_sync_runs(run_id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','success','empty','skipped','failed','partial')),
    planned_start TEXT,
    planned_end TEXT,
    row_count INTEGER NOT NULL DEFAULT 0 CHECK(row_count >= 0),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_first_limit_sync_items_resume
ON first_limit_sync_items(run_id, status);
