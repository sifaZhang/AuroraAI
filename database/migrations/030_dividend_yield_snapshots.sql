CREATE TABLE IF NOT EXISTS dividend_yield_snapshots (
 market TEXT NOT NULL, symbol TEXT NOT NULL, calculation_date TEXT NOT NULL,
 price_date TEXT, latest_price REAL, price_source TEXT, price_age_days INTEGER,
 latest_year INTEGER NOT NULL, latest_year_dps REAL,
 three_year_start INTEGER NOT NULL, three_year_end INTEGER NOT NULL,
 three_year_total_dps REAL, three_year_average_dps REAL,
 latest_year_yield REAL, three_year_average_yield REAL,
 data_quality_status TEXT NOT NULL, warning_flags TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(market,symbol,calculation_date)
);
