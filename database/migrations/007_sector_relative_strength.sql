-- Columns are added idempotently by database._migrate_sector_relative_strength
-- before this index is created, preserving all existing 70-point records.
CREATE INDEX IF NOT EXISTS idx_sector_scores_date_relative_strength
    ON sector_scores(trade_date, relative_strength_score DESC);
