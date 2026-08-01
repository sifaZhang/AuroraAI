CREATE TABLE IF NOT EXISTS industry_daily_scores (
 trade_date TEXT NOT NULL, classification TEXT NOT NULL, classification_version TEXT NOT NULL,
 industry_code TEXT NOT NULL, industry_level INTEGER NOT NULL CHECK(industry_level IN (1,2,3)),
 total_score REAL NOT NULL CHECK(total_score BETWEEN 0 AND 100),
 strength_score REAL NOT NULL, breadth_score REAL NOT NULL, strong_rise_score REAL NOT NULL,
 limit_score REAL NOT NULL, activity_score REAL NOT NULL, persistence_score REAL NOT NULL,
 quality_score REAL NOT NULL, turnover_ratio_5d REAL, turnover_ratio_20d REAL,
 median_turnover_ratio_20d REAL, price_volume_state TEXT NOT NULL,
 history_days_available INTEGER NOT NULL CHECK(history_days_available >= 0),
 rank_in_level INTEGER NOT NULL CHECK(rank_in_level > 0),
 industry_count_in_level INTEGER NOT NULL CHECK(industry_count_in_level > 0),
 percentile_in_level REAL NOT NULL CHECK(percentile_in_level BETWEEN 0 AND 1),
 confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low','unavailable')),
 score_version TEXT NOT NULL, evidence_json TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(trade_date,classification,classification_version,industry_code,score_version),
 FOREIGN KEY(trade_date,classification,classification_version,industry_code)
  REFERENCES industry_daily_snapshots(trade_date,classification,classification_version,industry_code)
);
CREATE INDEX IF NOT EXISTS idx_industry_scores_date_level_score
 ON industry_daily_scores(trade_date,industry_level,total_score DESC);
CREATE INDEX IF NOT EXISTS idx_industry_scores_code_date
 ON industry_daily_scores(industry_code,trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_industry_scores_date_rank
 ON industry_daily_scores(trade_date,industry_level,rank_in_level);
