-- Extend the PR6.2 shared run ledger with a local detection task type.
PRAGMA foreign_keys=OFF;
ALTER TABLE first_limit_sync_items RENAME TO first_limit_sync_items_legacy;
ALTER TABLE first_limit_sync_runs RENAME TO first_limit_sync_runs_legacy;
CREATE TABLE first_limit_sync_runs (
 run_id TEXT PRIMARY KEY, sync_type TEXT NOT NULL CHECK(sync_type IN ('calendar','securities','statuses','daily','minute','audit','detect')),
 parameters_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','success','partial','failed','interrupted')),
 data_source TEXT NOT NULL CHECK(data_source IN ('SINA','GM','CALCULATED','MANUAL','UNKNOWN')),
 planned_count INTEGER NOT NULL DEFAULT 0,success_count INTEGER NOT NULL DEFAULT 0,empty_count INTEGER NOT NULL DEFAULT 0,skipped_count INTEGER NOT NULL DEFAULT 0,failure_count INTEGER NOT NULL DEFAULT 0,inserted_rows INTEGER NOT NULL DEFAULT 0,updated_rows INTEGER NOT NULL DEFAULT 0,unchanged_rows INTEGER NOT NULL DEFAULT 0,retry_count INTEGER NOT NULL DEFAULT 0,last_error TEXT,is_dry_run INTEGER NOT NULL CHECK(is_dry_run IN (0,1)),sync_version TEXT NOT NULL,started_at TEXT,finished_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE first_limit_sync_items (run_id TEXT NOT NULL REFERENCES first_limit_sync_runs(run_id) ON DELETE CASCADE,item_key TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('pending','running','success','empty','skipped','failed','partial')),planned_start TEXT,planned_end TEXT,row_count INTEGER NOT NULL DEFAULT 0,retry_count INTEGER NOT NULL DEFAULT 0,last_error TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(run_id,item_key));
INSERT INTO first_limit_sync_runs SELECT * FROM first_limit_sync_runs_legacy;
INSERT INTO first_limit_sync_items SELECT * FROM first_limit_sync_items_legacy;
DROP TABLE first_limit_sync_items_legacy; DROP TABLE first_limit_sync_runs_legacy;
CREATE INDEX IF NOT EXISTS idx_first_limit_sync_items_resume ON first_limit_sync_items(run_id,status);
PRAGMA foreign_keys=ON;
