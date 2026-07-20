CREATE TABLE IF NOT EXISTS refresh_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL CHECK(job_type IN ('refresh_a_share','refresh_hk_prices','refresh_hk_ratings')),
    status TEXT NOT NULL CHECK(status IN ('pending','running','success','partial','failed')),
    total INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    no_data_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    progress_pct NUMERIC NOT NULL DEFAULT 0,
    current_code TEXT,
    message TEXT,
    error_summary TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_refresh_jobs_status ON refresh_jobs(status);
CREATE INDEX IF NOT EXISTS idx_refresh_jobs_type_created ON refresh_jobs(job_type,created_at);
