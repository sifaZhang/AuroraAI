ALTER TABLE daily_candidate_snapshots ADD COLUMN effective_score REAL;
ALTER TABLE daily_candidate_snapshots ADD COLUMN effective_rank INTEGER;
ALTER TABLE daily_candidate_snapshots ADD COLUMN capital_activity_score REAL;
ALTER TABLE daily_candidate_snapshots ADD COLUMN leader_score REAL;
ALTER TABLE daily_candidate_snapshots ADD COLUMN industry_trend_score REAL;
ALTER TABLE daily_candidate_snapshots ADD COLUMN industry_environment_score REAL;
ALTER TABLE daily_candidate_snapshots ADD COLUMN buy_recommendation TEXT;
ALTER TABLE daily_candidate_snapshots ADD COLUMN scoring_version TEXT;
ALTER TABLE daily_candidate_runs ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}';
