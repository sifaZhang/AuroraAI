import sqlite3
from datetime import date
import pytest

from backend.industry.models import EffectiveIndustryContext
from backend.strategy.first_limit.intraday_industry import (
    IntradayIndustryEstimator, completed_session_ratio,
)


def database(member_count=4):
    c=sqlite3.connect(":memory:");c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE industry_memberships_current(symbol TEXT,level3_code TEXT)")
    c.execute("CREATE TABLE first_limit_minute_bars(symbol TEXT,bar_time TEXT,timeframe TEXT,close REAL,amount REAL)")
    c.execute("CREATE TABLE a_share_daily_bars(stock_code TEXT,trade_date TEXT,adjustment TEXT,close REAL)")
    for index in range(member_count):
        symbol=f"00000{index}.SZ";c.execute("INSERT INTO industry_memberships_current VALUES(?, 'L3')",(symbol,))
        c.execute("INSERT INTO a_share_daily_bars VALUES(?,'2026-07-30','none',10)",(symbol.split('.')[0],))
        for stamp,close in (("14:30",10.2+index*.1),("14:55",10.3+index*.1),("15:00",1)):
            c.execute("INSERT INTO first_limit_minute_bars VALUES(?,?,?,?,100)",(symbol,f"2026-07-31T{stamp}:00+08:00","1m",close))
    return c


def context(): return EffectiveIndustryContext(3,"L3","三级",60,1,1,"high",None,"complete")


def test_estimate_uses_all_members_and_strict_1430_1455_cutoff():
    service=IntradayIndustryEstimator(database())
    early=service.estimate("000000.SZ",date(2026,7,31),"14:30",context())
    late=service.estimate("000000.SZ",date(2026,7,31),"14:55",context())
    assert early.valid_member_count==early.member_count==4
    assert early.equal_weight_return>0 and late.equal_weight_return>early.equal_weight_return
    assert late.equal_weight_return>-.5  # the stored 15:00 crash was not read
    assert early.turnover_estimated and early.data_cutoff.time().isoformat()=="14:30:00"


def test_partial_missing_and_unavailable_members():
    c=database();c.execute("DELETE FROM first_limit_minute_bars WHERE symbol='000003.SZ'")
    partial=IntradayIndustryEstimator(c).estimate("000000.SZ",date(2026,7,31),"14:55",context())
    assert partial.status=="partial" and partial.coverage_ratio==.75
    c.execute("DELETE FROM first_limit_minute_bars WHERE symbol<>'000000.SZ'")
    insufficient=IntradayIndustryEstimator(c).estimate("000000.SZ",date(2026,7,31),"14:55",context())
    assert insufficient.status=="intraday_data_insufficient" and insufficient.intraday_score is None


def test_elapsed_session_ratio_handles_lunch_and_rejects_other_times():
    assert completed_session_ratio(__import__('datetime').time(14,30)) == pytest.approx(210/240)
    assert completed_session_ratio(__import__('datetime').time(14,55)) == pytest.approx(235/240)
    with pytest.raises(ValueError): completed_session_ratio(__import__('datetime').time(14,29))
    with pytest.raises(ValueError): completed_session_ratio(__import__('datetime').time(15,0))
