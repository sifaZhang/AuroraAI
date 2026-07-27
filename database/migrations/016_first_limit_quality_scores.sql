CREATE TABLE IF NOT EXISTS first_limit_quality_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES first_limit_events(id) ON DELETE RESTRICT,
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    detection_version TEXT NOT NULL,
    scoring_version TEXT NOT NULL,
    score_status TEXT NOT NULL CHECK(score_status IN ('scored','zero_score','missing','indeterminate','excluded','approximate','error')),
    earned_score NUMERIC NOT NULL DEFAULT 0,
    theoretical_max_score NUMERIC NOT NULL DEFAULT 20 CHECK(theoretical_max_score = 20),
    determinable_max_score NUMERIC NOT NULL DEFAULT 0,
    coverage_ratio NUMERIC NOT NULL DEFAULT 0,
    is_complete INTEGER NOT NULL CHECK(is_complete IN (0,1)),
    is_approximate INTEGER NOT NULL CHECK(is_approximate IN (0,1)),
    reasons_json TEXT NOT NULL DEFAULT '[]',
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_id, scoring_version),
    UNIQUE(symbol, trade_date, detection_version, scoring_version)
);
CREATE INDEX IF NOT EXISTS idx_first_limit_quality_scores_date ON first_limit_quality_scores(trade_date, scoring_version, score_status);

CREATE TABLE IF NOT EXISTS first_limit_quality_components (
    score_id INTEGER NOT NULL REFERENCES first_limit_quality_scores(id) ON DELETE CASCADE,
    component_key TEXT NOT NULL,
    component_status TEXT NOT NULL CHECK(component_status IN ('scored','zero_score','missing','indeterminate','excluded','approximate','error')),
    earned_score NUMERIC,
    max_score NUMERIC NOT NULL,
    raw_value_json TEXT NOT NULL DEFAULT '{}',
    formula_version TEXT NOT NULL,
    source_table TEXT,
    source_date TEXT,
    source_version TEXT,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    is_approximate INTEGER NOT NULL CHECK(is_approximate IN (0,1)),
    PRIMARY KEY(score_id, component_key)
);

CREATE TABLE IF NOT EXISTS first_limit_quality_runs (
    run_id TEXT PRIMARY KEY,
    parameters_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed')),
    planned_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    is_dry_run INTEGER NOT NULL CHECK(is_dry_run IN (0,1)),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS first_limit_quality_run_items (
    run_id TEXT NOT NULL REFERENCES first_limit_quality_runs(run_id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success','skipped','failed')),
    score_id INTEGER REFERENCES first_limit_quality_scores(id) ON DELETE SET NULL,
    result_json TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, item_key)
);
