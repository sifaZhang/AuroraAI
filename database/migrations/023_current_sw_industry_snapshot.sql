CREATE TABLE IF NOT EXISTS industry_nodes (
    classification TEXT NOT NULL CHECK(classification = 'SW'),
    classification_version TEXT NOT NULL CHECK(classification_version = '2021'),
    industry_code TEXT NOT NULL,
    industry_name TEXT NOT NULL CHECK(length(trim(industry_name)) > 0),
    industry_level INTEGER NOT NULL CHECK(industry_level IN (1, 2, 3)),
    parent_code TEXT,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (classification, classification_version, industry_code),
    FOREIGN KEY (classification, classification_version, parent_code)
        REFERENCES industry_nodes(classification, classification_version, industry_code),
    CHECK((industry_level = 1 AND parent_code IS NULL) OR
          (industry_level IN (2, 3) AND parent_code IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_industry_nodes_level
ON industry_nodes(classification, classification_version, industry_level);

CREATE INDEX IF NOT EXISTS idx_industry_nodes_parent
ON industry_nodes(classification, classification_version, parent_code);

CREATE TABLE IF NOT EXISTS industry_memberships_current (
    classification TEXT NOT NULL CHECK(classification = 'SW'),
    classification_version TEXT NOT NULL CHECK(classification_version = '2021'),
    symbol TEXT NOT NULL,
    level1_code TEXT NOT NULL,
    level1_name TEXT NOT NULL,
    level2_code TEXT NOT NULL,
    level2_name TEXT NOT NULL,
    level3_code TEXT NOT NULL,
    level3_name TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (classification, classification_version, symbol),
    FOREIGN KEY (classification, classification_version, level1_code)
        REFERENCES industry_nodes(classification, classification_version, industry_code),
    FOREIGN KEY (classification, classification_version, level2_code)
        REFERENCES industry_nodes(classification, classification_version, industry_code),
    FOREIGN KEY (classification, classification_version, level3_code)
        REFERENCES industry_nodes(classification, classification_version, industry_code)
);

CREATE INDEX IF NOT EXISTS idx_industry_memberships_l1
ON industry_memberships_current(classification, classification_version, level1_code);

CREATE INDEX IF NOT EXISTS idx_industry_memberships_l2
ON industry_memberships_current(classification, classification_version, level2_code);

CREATE INDEX IF NOT EXISTS idx_industry_memberships_l3
ON industry_memberships_current(classification, classification_version, level3_code);
