CREATE TABLE IF NOT EXISTS minute_review_runs (
    run_id TEXT PRIMARY KEY,
    source_backtest_run_id TEXT NOT NULL REFERENCES backtest_runs(run_id) ON DELETE RESTRICT,
    parameters_json TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed')),
    review_version TEXT NOT NULL,
    data_cutoff TEXT NOT NULL,
    planned_count INTEGER NOT NULL DEFAULT 0 CHECK(planned_count >= 0),
    success_count INTEGER NOT NULL DEFAULT 0 CHECK(success_count >= 0),
    indeterminate_count INTEGER NOT NULL DEFAULT 0 CHECK(indeterminate_count >= 0),
    unresolved_count INTEGER NOT NULL DEFAULT 0 CHECK(unresolved_count >= 0),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count >= 0),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
    last_error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS minute_review_items (
    run_id TEXT NOT NULL REFERENCES minute_review_runs(run_id) ON DELETE CASCADE,
    source_trade_id INTEGER NOT NULL REFERENCES backtest_trades(id) ON DELETE RESTRICT,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success','indeterminate','unresolved','skipped','failed')),
    result_id INTEGER REFERENCES minute_review_results(id) ON DELETE SET NULL,
    error_type TEXT,
    last_error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id,source_trade_id)
);

CREATE TABLE IF NOT EXISTS minute_review_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES minute_review_runs(run_id) ON DELETE CASCADE,
    source_trade_id INTEGER NOT NULL REFERENCES backtest_trades(id) ON DELETE RESTRICT,
    source_signal_id INTEGER NOT NULL REFERENCES backtest_signals(id) ON DELETE RESTRICT,
    event_id INTEGER NOT NULL,
    observation_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    first_limit_date TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    o0 NUMERIC NOT NULL CHECK(o0 > 0),
    confirmation_status TEXT NOT NULL CHECK(confirmation_status IN ('confirmed','rejected','indeterminate')),
    confirmation_reason TEXT NOT NULL,
    confirmation_time TEXT,
    entry_price_raw NUMERIC,
    entry_price NUMERIC,
    entry_cost NUMERIC,
    stop_distance NUMERIC,
    data_quality_status TEXT NOT NULL CHECK(data_quality_status IN ('complete','indeterminate','unresolved')),
    classification TEXT NOT NULL,
    trading_day_offset INTEGER NOT NULL,
    board_bucket TEXT NOT NULL CHECK(board_bucket IN ('10pct','20pct','unknown')),
    protection_type TEXT NOT NULL CHECK(protection_type IN ('P1','P2','unknown')),
    market_environment TEXT NOT NULL,
    industry_environment TEXT NOT NULL,
    year INTEGER NOT NULL,
    audit_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id,source_trade_id)
);

CREATE TABLE IF NOT EXISTS minute_review_stop_results (
    review_result_id INTEGER NOT NULL REFERENCES minute_review_results(id) ON DELETE CASCADE,
    stop_rule TEXT NOT NULL CHECK(stop_rule IN ('S0','S1','S2','S3','S4')),
    status TEXT NOT NULL CHECK(status IN ('closed','unresolved','indeterminate')),
    trigger_time TEXT,
    trigger_price NUMERIC,
    trigger_reason TEXT,
    exit_time TEXT,
    exit_price_raw NUMERIC,
    exit_price NUMERIC,
    exit_cost NUMERIC,
    gross_return NUMERIC,
    net_return NUMERIC,
    max_drawdown NUMERIC,
    intraday_path_ambiguous INTEGER NOT NULL DEFAULT 0 CHECK(intraday_path_ambiguous IN (0,1)),
    delay_minutes INTEGER NOT NULL DEFAULT 0 CHECK(delay_minutes >= 0),
    audit_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(review_result_id,stop_rule)
);

CREATE TABLE IF NOT EXISTS minute_review_metrics (
    run_id TEXT NOT NULL REFERENCES minute_review_runs(run_id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    group_key TEXT NOT NULL,
    group_value TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY(run_id,scope,group_key,group_value)
);

CREATE INDEX IF NOT EXISTS idx_minute_review_results_symbol
ON minute_review_results(run_id,symbol,observation_date);

CREATE INDEX IF NOT EXISTS idx_minute_review_items_status
ON minute_review_items(run_id,status);
