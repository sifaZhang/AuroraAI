CREATE TABLE IF NOT EXISTS first_limit_context_scores (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id INTEGER NOT NULL REFERENCES first_limit_events(id) ON DELETE RESTRICT,
 observation_id INTEGER NOT NULL REFERENCES first_limit_pullback_observations(id) ON DELETE RESTRICT,
 symbol TEXT NOT NULL, first_limit_date TEXT NOT NULL, observation_date TEXT NOT NULL,
 detection_version TEXT NOT NULL, scoring_version TEXT NOT NULL, pullback_version TEXT NOT NULL, context_scoring_version TEXT NOT NULL,
 sector_trend_version TEXT, sector_breadth_version TEXT, sector_radar_version TEXT,
 score_status TEXT NOT NULL CHECK(score_status IN ('complete','partial','missing','indeterminate','approximate','error')),
 first_limit_score NUMERIC, pullback_score NUMERIC, industry_score NUMERIC, market_score NUMERIC, stock_trend_score NUMERIC,
 daily_base_score NUMERIC, daily_base_theoretical_max_score NUMERIC NOT NULL DEFAULT 90 CHECK(daily_base_theoretical_max_score=90),
 daily_base_determinable_max_score NUMERIC NOT NULL DEFAULT 0, daily_base_coverage_ratio NUMERIC NOT NULL DEFAULT 0,
 is_complete INTEGER NOT NULL CHECK(is_complete IN (0,1)), is_approximate INTEGER NOT NULL CHECK(is_approximate IN (0,1)),
 minute_confirm_score NUMERIC, minute_confirm_status TEXT NOT NULL CHECK(minute_confirm_status IN ('not_available','not_applicable','complete')),
 total_score NUMERIC, final_candidate_level TEXT NOT NULL CHECK(final_candidate_level IN ('pending_minute_confirmation','watch','indeterminate')),
 reasons_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(observation_id, context_scoring_version)
);
CREATE INDEX IF NOT EXISTS idx_first_limit_context_scores_date ON first_limit_context_scores(observation_date, context_scoring_version, score_status);
CREATE TABLE IF NOT EXISTS first_limit_context_components (
 score_id INTEGER NOT NULL REFERENCES first_limit_context_scores(id) ON DELETE CASCADE,
 component_key TEXT NOT NULL, component_status TEXT NOT NULL,
 earned_score NUMERIC, max_score NUMERIC NOT NULL, raw_value_json TEXT NOT NULL DEFAULT '{}',
 reasons_json TEXT NOT NULL DEFAULT '[]', source_table TEXT, source_date TEXT, source_version TEXT, is_approximate INTEGER NOT NULL CHECK(is_approximate IN (0,1)),
 PRIMARY KEY(score_id,component_key)
);
CREATE TABLE IF NOT EXISTS first_limit_context_runs (
 run_id TEXT PRIMARY KEY, parameters_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed')),
 planned_count INTEGER NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0, skipped_count INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0,
 indeterminate_count INTEGER NOT NULL DEFAULT 0, is_dry_run INTEGER NOT NULL CHECK(is_dry_run IN (0,1)), last_error TEXT,
 started_at TEXT NOT NULL, finished_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS first_limit_context_run_items (
 run_id TEXT NOT NULL REFERENCES first_limit_context_runs(run_id) ON DELETE CASCADE, item_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('success','skipped','failed','indeterminate')), score_id INTEGER REFERENCES first_limit_context_scores(id) ON DELETE SET NULL,
 result_json TEXT, last_error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(run_id,item_key)
);
