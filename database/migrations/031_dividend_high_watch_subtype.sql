PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

CREATE TABLE dividend_stable_universe_new (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    company_name TEXT NOT NULL,
    industry_level_1 TEXT,
    industry_level_2 TEXT,
    monopoly_type TEXT NOT NULL,
    stability_subtype TEXT NOT NULL CHECK(stability_subtype IN ('stable_monopoly','resource_monopoly_cyclical','high_dividend_watch')),
    inclusion_source TEXT NOT NULL CHECK(inclusion_source IN ('automatic_rule','manual_addition','manual_review')),
    inclusion_reason TEXT,
    risk_note TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0,1)),
    included_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (market, symbol)
);

INSERT INTO dividend_stable_universe_new (
    market,symbol,company_name,industry_level_1,industry_level_2,monopoly_type,
    stability_subtype,inclusion_source,inclusion_reason,risk_note,is_enabled,included_at,updated_at
)
SELECT
    market,symbol,company_name,industry_level_1,industry_level_2,monopoly_type,
    stability_subtype,inclusion_source,inclusion_reason,risk_note,is_enabled,included_at,updated_at
FROM dividend_stable_universe;

DROP TABLE dividend_stable_universe;
ALTER TABLE dividend_stable_universe_new RENAME TO dividend_stable_universe;

COMMIT;
PRAGMA foreign_keys = ON;
