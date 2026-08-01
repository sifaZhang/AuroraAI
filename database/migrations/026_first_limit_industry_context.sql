ALTER TABLE daily_candidate_snapshots ADD COLUMN sw_level1_code TEXT;
ALTER TABLE daily_candidate_snapshots ADD COLUMN sw_level2_code TEXT;
ALTER TABLE daily_candidate_snapshots ADD COLUMN sw_level3_code TEXT;
ALTER TABLE daily_candidate_snapshots ADD COLUMN effective_industry_level INTEGER CHECK(effective_industry_level IN (1,2,3));
ALTER TABLE daily_candidate_snapshots ADD COLUMN effective_industry_code TEXT;
ALTER TABLE daily_candidate_snapshots ADD COLUMN industry_context_status TEXT;
