CREATE TABLE IF NOT EXISTS daily_candidate_runs (
    run_id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('tail_preview','close_confirmed')),
    as_of TEXT NOT NULL,
    data_cutoff TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    detection_version TEXT NOT NULL,
    pullback_version TEXT NOT NULL,
    context_version TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    detection_complete INTEGER NOT NULL CHECK(detection_complete IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed')),
    planned_count INTEGER NOT NULL DEFAULT 0 CHECK(planned_count>=0),
    success_count INTEGER NOT NULL DEFAULT 0 CHECK(success_count>=0),
    indeterminate_count INTEGER NOT NULL DEFAULT 0 CHECK(indeterminate_count>=0),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count>=0),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count>=0),
    last_error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(trade_date,stage,parameter_hash)
);

CREATE TABLE IF NOT EXISTS daily_candidate_items (
    run_id TEXT NOT NULL REFERENCES daily_candidate_runs(run_id) ON DELETE CASCADE,
    first_limit_event_id INTEGER NOT NULL REFERENCES first_limit_events(id) ON DELETE RESTRICT,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','success','indeterminate','skipped','failed')),
    candidate_id INTEGER REFERENCES daily_candidate_snapshots(id) ON DELETE SET NULL,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
    error_type TEXT,
    last_error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id,first_limit_event_id)
);

CREATE TABLE IF NOT EXISTS daily_candidate_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES daily_candidate_runs(run_id) ON DELETE CASCADE,
    first_limit_event_id INTEGER NOT NULL REFERENCES first_limit_events(id) ON DELETE RESTRICT,
    trade_date TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('tail_preview','close_confirmed')),
    symbol TEXT NOT NULL,
    observation_day INTEGER,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN (
        'watching','eligible','pending_close_confirmation','confirmed',
        'eliminated','expired','indeterminate'
    )),
    candidate_grade TEXT CHECK(candidate_grade IS NULL OR candidate_grade IN ('S','A','B')),
    score NUMERIC,
    preview_candidate_id INTEGER REFERENCES daily_candidate_snapshots(id) ON DELETE SET NULL,
    change_type TEXT CHECK(change_type IS NULL OR change_type IN (
        'unchanged','upgraded','downgraded','newly_qualified','eliminated','preview_missing'
    )),
    detection_version TEXT NOT NULL,
    pullback_version TEXT NOT NULL,
    context_version TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    primary_reasons_json TEXT NOT NULL DEFAULT '[]',
    audit_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id,first_limit_event_id)
);

CREATE TABLE IF NOT EXISTS daily_candidate_evidence (
    candidate_id INTEGER NOT NULL REFERENCES daily_candidate_snapshots(id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    result TEXT NOT NULL CHECK(result IN ('pass','fail','unknown')),
    actual_value TEXT,
    threshold_value TEXT,
    unit TEXT,
    source_date TEXT,
    source_time TEXT,
    reason_code TEXT,
    display_text TEXT,
    ordinal INTEGER NOT NULL CHECK(ordinal>=0),
    PRIMARY KEY(candidate_id,rule_code)
);

CREATE INDEX IF NOT EXISTS idx_daily_candidate_runs_date_stage
ON daily_candidate_runs(trade_date,stage,status);

CREATE INDEX IF NOT EXISTS idx_daily_candidate_snapshots_daily
ON daily_candidate_snapshots(trade_date,stage,candidate_grade,symbol,first_limit_event_id);

CREATE INDEX IF NOT EXISTS idx_daily_candidate_items_status
ON daily_candidate_items(run_id,status);
