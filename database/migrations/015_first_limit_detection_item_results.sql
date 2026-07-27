-- Preserve the full PR6.3 detection outcome in the shared run ledger.
ALTER TABLE first_limit_sync_items ADD COLUMN result_json TEXT;
