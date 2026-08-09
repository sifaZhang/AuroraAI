import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.data_sources.models import ProviderResult, TradingDay
from backend.industry.refresh_service import IndustryRadarRefreshService, resolve_target_trade_date_from_calendar
from backend.industry.score_service import build_industry_scores


def database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    root = Path(__file__).resolve().parents[1] / "database" / "migrations"
    for number in (23, 24, 25):
        connection.executescript(next(root.glob(f"{number:03d}_*.sql")).read_text(encoding="utf-8"))
    now = "2026-08-01T00:00:00+00:00"
    connection.execute("INSERT INTO industry_nodes VALUES('SW','2021','L1','一级',1,NULL,'test',?)", (now,))
    connection.execute("INSERT INTO industry_nodes VALUES('SW','2021','L2','二级',2,'L1','test',?)", (now,))
    connection.execute("INSERT INTO industry_nodes VALUES('SW','2021','L3','三级',3,'L2','test',?)", (now,))
    return connection


def snapshot_values(day, code, level):
    return (str(day), "SW", "2021", code, level, 3, 3, 3, 0, 0, 1.0, 1.0, 1.0,
            2, 1, 0, 2 / 3, 1 / 3, 1, 1 / 3, 0, 0, None, None, 100.0, 30.0,
            "complete", "{}", "now")


def complete_day(connection, day):
    for code, level in (("L1", 1), ("L2", 2), ("L3", 3)):
        connection.execute("INSERT INTO industry_daily_snapshots VALUES(" + ",".join("?" * 29) + ")", snapshot_values(day, code, level))
    build_industry_scores(connection=connection, trade_date=day)


def test_target_date_uses_shanghai_close_and_auckland_dst_conversion():
    days = (date(2026, 1, 29), date(2026, 1, 30), date(2026, 7, 30), date(2026, 7, 31))
    # NZ standard time 19:09/19:10 maps to Shanghai 15:09/15:10.
    before = datetime(2026, 7, 31, 19, 9, tzinfo=ZoneInfo("Pacific/Auckland"))
    after = datetime(2026, 7, 31, 19, 10, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert resolve_target_trade_date_from_calendar(now=before, open_trade_dates=days) == date(2026, 7, 30)
    assert resolve_target_trade_date_from_calendar(now=after, open_trade_dates=days) == date(2026, 7, 31)
    # NZ daylight time 20:09/20:10 maps to Shanghai 15:09/15:10.
    dst_before = datetime(2026, 1, 30, 20, 9, tzinfo=ZoneInfo("Pacific/Auckland"))
    dst_after = datetime(2026, 1, 30, 20, 10, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert resolve_target_trade_date_from_calendar(now=dst_before, open_trade_dates=days) == date(2026, 1, 29)
    assert resolve_target_trade_date_from_calendar(now=dst_after, open_trade_dates=days) == date(2026, 1, 30)


def test_missing_dates_detects_middle_score_gap_in_trade_day_order():
    connection = database()
    complete_day(connection, date(2026, 7, 30))
    complete_day(connection, date(2026, 8, 3))
    connection.execute("DELETE FROM industry_daily_scores WHERE trade_date='2026-07-31'")
    days = tuple(date.fromisoformat(item) for item in ("2026-07-30", "2026-07-31", "2026-08-03"))
    service = IndustryRadarRefreshService(connection)
    assert service.get_industry_date_status(trade_date=date(2026, 7, 30)).complete
    assert service.find_missing_industry_dates(target_trade_date=date(2026, 8, 3), open_trade_dates=days, start_date=date(2026, 7, 30)) == [date(2026, 7, 31)]


class CalendarProvider:
    def __init__(self, days): self.days, self.calls = days, 0
    def list_calendar_days(self, **_kwargs):
        self.calls += 1
        now = datetime(2026, 8, 3, tzinfo=ZoneInfo("Asia/Shanghai"))
        return ProviderResult(self.days, "test", now, now, len(self.days))


def test_dry_run_uses_one_calendar_range_for_all_missing_dates():
    connection = database()
    complete_day(connection, date(2026, 7, 30))
    provider = CalendarProvider([
        TradingDay(date(2026, 7, 30), True, None, "test"),
        TradingDay(date(2026, 7, 31), True, date(2026, 7, 30), "test"),
        TradingDay(date(2026, 8, 1), False, date(2026, 7, 31), "test"),
        TradingDay(date(2026, 8, 3), True, date(2026, 7, 31), "test"),
    ])
    service = IndustryRadarRefreshService(connection, calendar_provider=provider,
        now_factory=lambda: datetime(2026, 8, 3, 15, 11, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = service.refresh(dry_run=True)
    assert result.missing_trade_dates == (date(2026, 7, 31), date(2026, 8, 3))
    assert provider.calls == 1


def test_five_missing_trade_days_keep_the_level_three_path():
    connection = database()
    complete_day(connection, date(2026, 7, 30))
    days = tuple(date.fromisoformat(item) for item in (
        "2026-07-30", "2026-07-31", "2026-08-03", "2026-08-04", "2026-08-05",
        "2026-08-06", "2026-08-07",
    ))
    service = IndustryRadarRefreshService(connection)

    assert service.find_missing_industry_dates(
        target_trade_date=date(2026, 8, 7), open_trade_dates=days,
    ) == list(days[1:])
    status = service.get_industry_date_status(trade_date=date(2026, 7, 30))
    assert status.complete
    assert status.node_counts[3] == status.snapshot_counts[3] == status.score_counts[3] == 1


def test_existing_complete_date_does_not_write_duplicate_snapshots_or_scores():
    connection = database()
    target = date(2026, 7, 30)
    complete_day(connection, target)
    before = (
        connection.execute("SELECT COUNT(*) FROM industry_daily_snapshots WHERE trade_date=?", (str(target),)).fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM industry_daily_scores WHERE trade_date=?", (str(target),)).fetchone()[0],
    )
    provider = CalendarProvider([TradingDay(target, True, None, "test")])
    result = IndustryRadarRefreshService(
        connection, calendar_provider=provider,
        now_factory=lambda: datetime(2026, 7, 30, 15, 11, tzinfo=ZoneInfo("Asia/Shanghai")),
        daily_syncer=lambda _day: (_ for _ in ()).throw(AssertionError("must not sync complete date")),
    ).refresh(target_trade_date=target)

    after = (
        connection.execute("SELECT COUNT(*) FROM industry_daily_snapshots WHERE trade_date=?", (str(target),)).fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM industry_daily_scores WHERE trade_date=?", (str(target),)).fetchone()[0],
    )
    assert result.status == "no_work"
    assert after == before == (3, 3)
