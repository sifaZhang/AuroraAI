from datetime import date

from backend.dividend.annual_dps import METHOD, aggregate_events
from backend.dividend.models import DividendEvent


def event(end_date, ex_date, cash=1.0, procedure="实施", ann_date=date(2026, 3, 1)):
    return DividendEvent("601088.SH", ann_date, ex_date, cash, procedure, end_date)


def test_report_period_not_ex_date_controls_year_and_midyear_plus_annual_sum():
    totals, counts = aggregate_events([
        event(date(2024, 12, 31), date(2025, 7, 7), 2.26),
        event(date(2025, 6, 30), date(2025, 11, 10), .98),
        event(date(2025, 12, 31), date(2026, 7, 13), 1.03),
    ], (2023, 2024, 2025))
    assert totals["601088.SH"] == {2024: 2.26, 2025: 2.01}
    assert counts["601088.SH"] == {2024: 1, 2025: 2}
    assert METHOD == "implemented_cash_dividend_grouped_by_end_date"


def test_invalid_and_duplicate_events_do_not_change_report_period_total():
    duplicate = event(date(2025, 6, 30), date(2025, 11, 10), .98)
    totals, counts = aggregate_events([
        duplicate, duplicate, event(date(2025, 12, 31), date(2026, 7, 13), 1.03),
        event(date(2025, 12, 31), date(2026, 7, 13), 9, "预案"),
        event(date(2025, 12, 31), date(2026, 7, 13), 0),
    ], (2023, 2024, 2025))
    assert totals["601088.SH"][2025] == 2.01
    assert counts["601088.SH"][2025] == 2
