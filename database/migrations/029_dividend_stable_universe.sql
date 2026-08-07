CREATE TABLE IF NOT EXISTS dividend_stable_universe (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    company_name TEXT NOT NULL,
    industry_level_1 TEXT,
    industry_level_2 TEXT,
    monopoly_type TEXT NOT NULL,
    stability_subtype TEXT NOT NULL CHECK(stability_subtype IN ('stable_monopoly','resource_monopoly_cyclical')),
    inclusion_source TEXT NOT NULL CHECK(inclusion_source IN ('automatic_rule','manual_addition','manual_review')),
    inclusion_reason TEXT,
    risk_note TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0,1)),
    included_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (market, symbol)
);

CREATE TABLE IF NOT EXISTS annual_cash_dividend_summaries (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    calendar_year INTEGER NOT NULL,
    cash_dividend_per_share REAL NOT NULL CHECK(cash_dividend_per_share > 0),
    dividend_event_count INTEGER NOT NULL CHECK(dividend_event_count > 0),
    calculation_method TEXT NOT NULL,
    source TEXT NOT NULL,
    data_quality_status TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (market, symbol, calendar_year),
    FOREIGN KEY (market, symbol) REFERENCES dividend_stable_universe(market, symbol)
);

CREATE INDEX IF NOT EXISTS idx_annual_cash_dividend_symbol_year
ON annual_cash_dividend_summaries(symbol, calendar_year);
