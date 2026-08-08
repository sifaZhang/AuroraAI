from backend.dividend.high_dividend_watch_service import classify_industry, qualify_historical_dividend


def test_each_year_threshold_is_required():
    assert not qualify_historical_dividend(
        {2023: 4, 2024: 4, 2025: 4}, {2023: 100, 2024: 100, 2025: 100}
    )[1]
    assert qualify_historical_dividend(
        {2023: 10, 2024: 1, 2025: 1}, {2023: 100, 2024: 100, 2025: 100}
    )[1]


def test_types_are_only_three_values():
    assert classify_industry("\u94f6\u884c") == "stable_monopoly"
    assert classify_industry("\u7164\u70ad") == "resource_monopoly_cyclical"
    assert classify_industry("\u6d88\u8d39") == "high_dividend_watch"
