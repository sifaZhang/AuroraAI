CREATE TABLE IF NOT EXISTS first_limit_pipeline_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('tail_preview','close_confirmed')),
    as_of TEXT NOT NULL,
    data_cutoff TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('full_market','partial')),
    universe_version TEXT NOT NULL,
    parameter_json TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending','running','success','partial','failed','cancelled','interrupted'
    )),
    current_step TEXT,
    progress_current INTEGER NOT NULL DEFAULT 0 CHECK(progress_current >= 0),
    progress_total INTEGER CHECK(progress_total IS NULL OR progress_total >= 0),
    progress_percent NUMERIC CHECK(
        progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100
    ),
    message TEXT,
    candidate_run_id TEXT REFERENCES daily_candidate_runs(run_id) ON DELETE SET NULL,
    coverage_complete INTEGER NOT NULL DEFAULT 0
        CHECK(coverage_complete IN (0,1)),
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT,
    error_code TEXT,
    error_message TEXT,
    UNIQUE(trade_date, stage, parameter_hash)
);

CREATE INDEX IF NOT EXISTS idx_first_limit_pipeline_jobs_status
ON first_limit_pipeline_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS first_limit_pipeline_steps (
    job_id INTEGER NOT NULL REFERENCES first_limit_pipeline_jobs(id) ON DELETE CASCADE,
    step_code TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending','running','success','partial','failed','skipped','interrupted'
    )),
    progress_current INTEGER NOT NULL DEFAULT 0 CHECK(progress_current >= 0),
    progress_total INTEGER CHECK(progress_total IS NULL OR progress_total >= 0),
    started_at TEXT,
    finished_at TEXT,
    input_summary_json TEXT NOT NULL DEFAULT '{}',
    output_summary_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    PRIMARY KEY(job_id, step_code)
);

CREATE TABLE IF NOT EXISTS first_limit_pipeline_coverage (
    job_id INTEGER NOT NULL REFERENCES first_limit_pipeline_jobs(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    required_start TEXT,
    required_end TEXT,
    expected_count INTEGER CHECK(expected_count IS NULL OR expected_count >= 0),
    covered_count INTEGER NOT NULL DEFAULT 0 CHECK(covered_count >= 0),
    missing_count INTEGER NOT NULL DEFAULT 0 CHECK(missing_count >= 0),
    coverage_ratio NUMERIC CHECK(
        coverage_ratio IS NULL OR coverage_ratio BETWEEN 0 AND 1
    ),
    complete INTEGER NOT NULL CHECK(complete IN (0,1)),
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(job_id, domain)
);

CREATE TABLE IF NOT EXISTS first_limit_pipeline_universe (
    job_id INTEGER NOT NULL REFERENCES first_limit_pipeline_jobs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
    exclusion_reason TEXT,
    source_cutoff TEXT NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(job_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_first_limit_pipeline_universe_eligible
ON first_limit_pipeline_universe(job_id, eligible, symbol);

CREATE TABLE IF NOT EXISTS first_limit_pipeline_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES first_limit_pipeline_jobs(id) ON DELETE CASCADE,
    step_code TEXT NOT NULL,
    symbol TEXT,
    trade_date TEXT,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, step_code, symbol, trade_date, error_code)
);

CREATE INDEX IF NOT EXISTS idx_first_limit_pipeline_failures_page
ON first_limit_pipeline_failures(job_id, id);
