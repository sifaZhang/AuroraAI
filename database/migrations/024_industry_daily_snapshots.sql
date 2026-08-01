CREATE TABLE IF NOT EXISTS industry_daily_snapshots (
    trade_date TEXT NOT NULL,
    classification TEXT NOT NULL,
    classification_version TEXT NOT NULL,
    industry_code TEXT NOT NULL,
    industry_level INTEGER NOT NULL CHECK(industry_level IN (1, 2, 3)),
    constituent_count INTEGER NOT NULL CHECK(constituent_count >= 0),
    eligible_count INTEGER NOT NULL CHECK(eligible_count >= 0),
    valid_bar_count INTEGER NOT NULL CHECK(valid_bar_count >= 0),
    missing_bar_count INTEGER NOT NULL CHECK(missing_bar_count >= 0),
    suspended_count INTEGER NOT NULL CHECK(suspended_count >= 0),
    coverage_ratio REAL NOT NULL CHECK(coverage_ratio BETWEEN 0 AND 1),
    equal_weight_return REAL,
    median_return REAL,
    rise_count INTEGER NOT NULL CHECK(rise_count >= 0),
    fall_count INTEGER NOT NULL CHECK(fall_count >= 0),
    flat_count INTEGER NOT NULL CHECK(flat_count >= 0),
    rise_ratio REAL,
    fall_ratio REAL,
    strong_rise_count INTEGER NOT NULL CHECK(strong_rise_count >= 0),
    strong_rise_ratio REAL,
    limit_up_count INTEGER NOT NULL CHECK(limit_up_count >= 0),
    limit_down_count INTEGER NOT NULL CHECK(limit_down_count >= 0),
    first_limit_count INTEGER,
    broken_limit_count INTEGER,
    turnover_amount REAL,
    median_turnover_amount REAL,
    data_status TEXT NOT NULL CHECK(data_status IN ('complete','partial','insufficient','empty')),
    source_snapshot TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, classification, classification_version, industry_code),
    FOREIGN KEY (classification, classification_version, industry_code)
        REFERENCES industry_nodes(classification, classification_version, industry_code)
);

CREATE INDEX IF NOT EXISTS idx_industry_daily_snapshots_date_level
ON industry_daily_snapshots(trade_date, industry_level);

CREATE INDEX IF NOT EXISTS idx_industry_daily_snapshots_code_date
ON industry_daily_snapshots(industry_code, trade_date);
