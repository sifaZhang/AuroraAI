from datetime import date

import pytest

from backend.dividend.models import DividendEvent
from backend.dividend.share_basis_adjustment import current_basis_dps, current_yield_metrics


def event(symbol, end, ex, cash=0, status="实施", stock=0, bonus=0, capital=0):
    return DividendEvent(symbol, None, ex, cash, status, end, stk_div=stock, stk_bo_rate=bonus, stk_co_rate=capital)


def adjusted(events, as_of=date(2026, 8, 9)):
    return current_basis_dps(events, (2023, 2024, 2025), as_of)[0]["000001.SZ"]


def test_no_expansion_leaves_raw_dps_unchanged():
    assert adjusted([event("000001.SZ", date(2023,12,31), date(2024,5,1), 1)]) == {2023: 1}


def test_single_10_for_4_adjusts_earlier_cash_and_same_day_cash():
    values = adjusted([
        event("000001.SZ", date(2023,12,31), date(2024,5,1), .9),
        event("000001.SZ", date(2024,12,31), date(2025,6,17), .9, stock=.4),
    ])
    assert values[2023] == pytest.approx(.9 / 1.4)
    assert values[2024] == pytest.approx(.9 / 1.4)


def test_two_expansions_compound():
    assert adjusted([event("000001.SZ",date(2023,12,31),date(2024,1,1),1),event("000001.SZ",date(2024,12,31),date(2025,1,1),0,stock=.4),event("000001.SZ",date(2025,12,31),date(2026,1,1),0,stock=.2)])[2023] == pytest.approx(1/1.68)


def test_cash_split_cash_processes_each_event_individually():
    values=adjusted([event("000001.SZ",date(2025,3,31),date(2025,1,1),.5),event("000001.SZ",date(2025,6,30),date(2025,6,1),0,stock=.5),event("000001.SZ",date(2025,9,30),date(2025,11,1),.3)])
    assert values[2025] == pytest.approx(.5/1.5+.3)


def test_unimplemented_or_future_expansion_does_not_adjust():
    values=adjusted([event("000001.SZ",date(2023,12,31),date(2024,1,1),1),event("000001.SZ",date(2024,12,31),None,0,"股东大会通过",stock=.4),event("000001.SZ",date(2025,12,31),date(2027,1,1),0,stock=.4)])
    assert values[2023] == 1


def test_metrics_stability_boundaries():
    stable=current_yield_metrics({2023:1.45,2024:1.41,2025:1.45},(2023,2024,2025),10)
    variable=current_yield_metrics({2023:1,2024:1.5,2025:1},(2023,2024,2025),10)
    high=current_yield_metrics({2023:.49,2024:.5,2025:2.08},(2023,2024,2025),10)
    assert stable['dividend_stability']=='stable' and stable['dividend_variation_ratio']==pytest.approx(1.45/1.41)
    assert variable['dividend_stability']=='variable'
    assert high['dividend_stability']=='highly_variable' and high['conservative_three_year_current_yield']==pytest.approx(.049)
