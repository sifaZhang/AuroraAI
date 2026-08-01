from dataclasses import replace
from datetime import date

from backend.data_sources.industry_sync.service import _resolve_memberships
from backend.data_sources.models import IndustryMembership


def membership(*, symbol="600519.SH", level3_code="850111", in_date=None):
    return IndustryMembership(
        "SW", "2021", symbol, "sample", "801000", "level1",
        "801010", "level2", level3_code, f"level3-{level3_code}",
        in_date, None, True, "fixture",
    )


def test_only_latest_current_membership_is_selected():
    old = membership(level3_code="850111", in_date=date(2022, 7, 29))
    latest = membership(level3_code="850112", in_date=date(2026, 7, 1))

    valid, conflicts, duplicates = _resolve_memberships([old, latest])

    assert valid == [latest]
    assert conflicts == []
    assert duplicates == 0


def test_identical_rows_at_latest_date_are_deduplicated():
    old = membership(level3_code="850110", in_date=date(2022, 7, 29))
    latest = membership(level3_code="850112", in_date=date(2026, 7, 1))

    valid, conflicts, duplicates = _resolve_memberships([old, latest, latest])

    assert valid == [latest]
    assert conflicts == []
    assert duplicates == 1


def test_different_memberships_at_latest_date_are_reported_as_conflict():
    effective_date = date(2026, 7, 1)
    first = membership(level3_code="850111", in_date=effective_date)
    second = membership(level3_code="850112", in_date=effective_date)

    valid, conflicts, duplicates = _resolve_memberships([first, second])

    assert valid == []
    assert len(conflicts) == 1
    assert conflicts[0].symbol == "600519.SH"
    assert conflicts[0].candidates == (first, second)
    assert duplicates == 0


def test_undated_rows_are_ignored_when_a_dated_row_exists():
    undated = membership(level3_code="850111")
    dated = membership(level3_code="850112", in_date=date(2026, 7, 1))

    valid, conflicts, duplicates = _resolve_memberships([undated, dated])

    assert valid == [dated]
    assert conflicts == []
    assert duplicates == 0


def test_undated_rows_are_compared_when_all_dates_are_missing():
    first = membership(level3_code="850111")
    second = replace(first, level3_code="850112", level3_name="level3-850112")

    valid, conflicts, duplicates = _resolve_memberships([first, second])

    assert valid == []
    assert len(conflicts) == 1
    assert duplicates == 0
