"""Single minimal high-dividend candidate rule."""
from __future__ import annotations

MIN_HISTORICAL_ANNUAL_YIELD = .04


def qualify_historical_dividend(dps, prices, minimum=MIN_HISTORICAL_ANNUAL_YIELD):
    yields = {}
    failures = []
    for year, value in dps.items():
        price = prices.get(year)
        if value is None or value <= 0:
            failures.append(f"missing_dps_{year}")
        elif price is None or price <= 0:
            failures.append(f"missing_reference_price_{year}")
        else:
            yields[year] = value / price
            if yields[year] < minimum:
                failures.append(f"below_min_annual_yield_{year}")
    return yields, failures


def classify_industry(industry):
    text = industry or ""
    resource_keywords = ("\u7164\u70ad", "\u77f3\u6cb9", "\u77f3\u5316", "\u822a\u8fd0", "\u6709\u8272", "\u5316\u5de5", "\u6c7d\u8f66")
    stable_keywords = ("\u94f6\u884c", "\u6c34\u7535", "\u6838\u7535", "\u7535\u4fe1", "\u9ad8\u901f", "\u516c\u7528\u4e8b\u4e1a", "\u94c1\u8def")
    if any(keyword in text for keyword in resource_keywords):
        return "resource_monopoly_cyclical"
    if any(keyword in text for keyword in stable_keywords):
        return "stable_monopoly"
    return "high_dividend_watch"
