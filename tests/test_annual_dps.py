from datetime import date

import pytest

from backend.dividend.annual_dps import METHOD, aggregate_events, select_effective_dividend_events
from backend.dividend.models import DividendEvent


def event(
    end_date,
    cash=1.0,
    procedure="\u5b9e\u65bd",
    ann_date=date(2026, 3, 1),
    *,
    symbol="601088.SH",
    ex_date=None,
    record_date=None,
    pay_date=None,
    imp_ann_date=None,
    base_date=None,
):
    return DividendEvent(
        symbol, ann_date, ex_date, cash, procedure, end_date,
        record_date, pay_date, imp_ann_date, base_date,
    )


def test_status_priority_collapses_one_lifecycle_once():
    rows = [
        event(date(2025, 12, 31), .5, "\u9884\u6848", date(2026, 3, 1)),
        event(date(2025, 12, 31), .5, "\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7", date(2026, 5, 1)),
        event(date(2025, 12, 31), .5, "\u5b9e\u65bd", None, imp_ann_date=date(2026, 6, 1), ex_date=date(2026, 6, 8)),
    ]
    selected = select_effective_dividend_events(rows, (2025,))
    totals, counts = aggregate_events(rows, (2025,))
    assert [row.div_proc for row in selected] == ["\u5b9e\u65bd"]
    assert totals["601088.SH"][2025] == .5
    assert counts["601088.SH"][2025] == 1


def test_shareholder_approved_wins_over_proposal_and_counts_alone():
    rows = [
        event(date(2025, 12, 31), .5, "\u9884\u6848", date(2026, 3, 1)),
        event(date(2025, 12, 31), .5, "\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7", date(2026, 5, 1)),
    ]
    selected = select_effective_dividend_events(rows, (2025,))
    totals, _ = aggregate_events(rows, (2025,))
    assert [row.div_proc for row in selected] == ["\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7"]
    assert totals["601088.SH"][2025] == .5


@pytest.mark.parametrize("procedure", ["\u9884\u6848", "\u505c\u6b62\u5b9e\u65bd", "\u53d6\u6d88", "\u5426\u51b3"])
def test_non_formal_or_cancelled_status_does_not_count(procedure):
    totals, counts = aggregate_events([event(date(2025, 12, 31), .5, procedure)], (2025,))
    assert totals["601088.SH"].get(2025, 0) == 0
    assert counts["601088.SH"].get(2025, 0) == 0


def test_cancellation_after_approval_or_implementation_removes_plan():
    for prior in ("\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7", "\u5b9e\u65bd"):
        rows = [
            event(date(2025, 12, 31), .5, prior, date(2026, 5, 1)),
            event(date(2025, 12, 31), .5, "\u505c\u6b62\u5b9e\u65bd", date(2026, 6, 1)),
        ]
        assert select_effective_dividend_events(rows, (2025,)) == []


def test_report_period_controls_year_and_different_payments_are_preserved():
    rows = [
        event(date(2024, 12, 31), 2.26, ex_date=date(2025, 7, 7)),
        event(date(2025, 6, 30), .98, ex_date=date(2025, 11, 10)),
        event(date(2025, 12, 31), 1.03, ex_date=date(2026, 7, 13)),
    ]
    totals, counts = aggregate_events(rows, (2023, 2024, 2025))
    assert totals["601088.SH"] == {2024: 2.26, 2025: 2.01}
    assert counts["601088.SH"] == {2024: 1, 2025: 2}
    assert METHOD == "effective_cash_dividend_grouped_by_end_date_v2"


def test_same_report_period_same_amount_distinct_payouts_are_not_merged():
    rows = [
        event(date(2025, 12, 31), 1.0, ex_date=date(2026, 6, 1), imp_ann_date=date(2026, 5, 20)),
        event(date(2025, 12, 31), 1.0, ex_date=date(2026, 12, 1), imp_ann_date=date(2026, 11, 20)),
    ]
    totals, counts = aggregate_events(rows, (2025,))
    assert totals["601088.SH"][2025] == 2.0
    assert counts["601088.SH"][2025] == 2


def test_same_report_period_different_implemented_payments_are_preserved():
    rows = [
        event(date(2025, 12, 31), .4, ex_date=date(2026, 6, 1), imp_ann_date=date(2026, 5, 20)),
        event(date(2025, 12, 31), .6, ex_date=date(2026, 12, 1), imp_ann_date=date(2026, 11, 20)),
    ]
    totals, counts = aggregate_events(rows, (2025,))
    assert totals["601088.SH"][2025] == 1.0
    assert counts["601088.SH"][2025] == 2


def test_exact_duplicate_is_counted_once():
    duplicate = event(date(2025, 6, 30), .98, ex_date=date(2025, 11, 10))
    totals, counts = aggregate_events([duplicate, duplicate], (2025,))
    assert totals["601088.SH"][2025] == .98
    assert counts["601088.SH"][2025] == 1


def test_revised_amount_in_same_lifecycle_replaces_old_amount():
    rows = [
        event(date(2023, 12, 31), .356, "\u9884\u6848", date(2024, 3, 22)),
        event(date(2023, 12, 31), .356, "\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7", date(2024, 3, 22), base_date=date(2024, 3, 22)),
        event(date(2023, 12, 31), .3261, "\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7", date(2024, 3, 22), base_date=date(2024, 7, 2)),
        event(date(2023, 12, 31), .3261, "\u5b9e\u65bd", date(2024, 3, 22), imp_ann_date=date(2024, 7, 3)),
    ]
    totals, counts = aggregate_events(rows, (2023,))
    assert totals["601088.SH"][2023] == .3261
    assert counts["601088.SH"][2023] == 1


def test_audited_2025_real_samples():
    rows = [
        event(date(2025, 9, 30), 1.0, symbol="000651.SZ", ann_date=date(2025, 11, 25), ex_date=date(2026, 1, 23), imp_ann_date=date(2026, 1, 16)),
        event(date(2025, 12, 31), 2.0, "\u80a1\u4e1c\u5927\u4f1a\u901a\u8fc7", date(2026, 7, 1), symbol="000651.SZ"),
        event(date(2025, 9, 30), .21, symbol="600900.SH", ann_date=None, ex_date=date(2026, 2, 12), imp_ann_date=date(2026, 2, 5)),
        event(date(2025, 12, 31), .79, symbol="600900.SH", ann_date=date(2026, 5, 22), ex_date=date(2026, 7, 17), imp_ann_date=date(2026, 7, 10)),
        event(date(2025, 6, 30), .088, symbol="600028.SH", ann_date=None, ex_date=date(2025, 9, 12), imp_ann_date=date(2025, 9, 6)),
        event(date(2025, 12, 31), .112, symbol="600028.SH", ann_date=date(2026, 5, 14), ex_date=date(2026, 6, 17), imp_ann_date=date(2026, 6, 10)),
    ]
    totals, _ = aggregate_events(rows, (2025,))
    assert totals["000651.SZ"][2025] == 3.0
    assert totals["600900.SH"][2025] == 1.0
    assert totals["600028.SH"][2025] == pytest.approx(.2)
