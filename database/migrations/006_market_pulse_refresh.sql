-- refresh_jobs is rebuilt once by database.py to add the fixed Market Pulse
-- task type and source column while preserving all existing job history.
CREATE INDEX IF NOT EXISTS idx_refresh_jobs_market_pulse_active
    ON refresh_jobs(job_type, source, status);
CREATE INDEX IF NOT EXISTS idx_refresh_jobs_status ON refresh_jobs(status);
CREATE INDEX IF NOT EXISTS idx_refresh_jobs_type_created ON refresh_jobs(job_type,created_at);
