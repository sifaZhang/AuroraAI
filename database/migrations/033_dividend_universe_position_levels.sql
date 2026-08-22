ALTER TABLE dividend_stable_universe ADD COLUMN grade TEXT CHECK(grade IN ('S','A','B'));
ALTER TABLE dividend_stable_universe ADD COLUMN entry_yield REAL CHECK(entry_yield IS NULL OR entry_yield >= 0);
ALTER TABLE dividend_stable_universe ADD COLUMN add_yield REAL CHECK(add_yield IS NULL OR add_yield >= 0);
ALTER TABLE dividend_stable_universe ADD COLUMN heavy_yield REAL CHECK(heavy_yield IS NULL OR heavy_yield >= 0);
