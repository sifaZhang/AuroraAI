-- sector_source_status is rebuilt once by database.py to widen its status
-- constraint without creating a duplicate health table.
CREATE INDEX IF NOT EXISTS idx_sector_source_status_health_status
    ON sector_source_status(status);
