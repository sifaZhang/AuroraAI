import pandas as pd

from backend.collector import probe_sector_data as probe


def industry_list(code="801011", name="林业Ⅱ"):
    return pd.DataFrame({"指数代码": [code], "指数名称": [name]})


def history():
    return pd.DataFrame(
        {"日期": pd.date_range("2026-06-01", periods=25), "收盘": range(1, 26), "成交量": [10] * 24 + [100]}
    )


class FakeAk:
    def index_realtime_sw(self, symbol):
        assert symbol in {"一级行业", "二级行业"}
        return industry_list()

    def index_hist_sw(self, symbol, period):
        return history()

    def index_component_sw(self, symbol):
        return pd.DataFrame({"证券代码": ["000001"], "证券名称": ["平安银行"]})

    def stock_board_industry_name_em(self):
        raise ConnectionError("eastmoney unavailable")


def test_sw_l2_filter_and_unified_source_fields():
    _, industries = probe.load_industries(FakeAk(), "sw_l2")
    assert industries[0].source == "sw_l2"
    assert industries[0].level == 2
    trend = probe.to_trend(industries[0], probe.load_history(FakeAk(), industries[0]))
    assert trend.source == "sw_l2"
    assert trend.sector_level == 2
    assert trend.trend_score == 70


def test_sw_l1_source_and_sector_level_are_explicit():
    _, industries = probe.load_industries(FakeAk(), "sw_l1")
    trend = probe.to_trend(industries[0], probe.load_history(FakeAk(), industries[0]))
    assert trend.source == "sw_l1"
    assert trend.sector_level == 1


def test_source_prefix_prevents_cross_source_key_collision():
    sw_l1 = probe.Industry("sw_l1", "BK001", "行业A", 1)
    sw = probe.Industry("sw_l2", "BK001", "行业A", 2)
    em = probe.Industry("eastmoney", "BK001", "行业A", "industry")
    assert len({sw_l1.unique_key, sw.unique_key, em.unique_key}) == 3


def test_all_succeeds_when_sw_l1_works_and_fine_sources_are_unavailable(monkeypatch):
    monkeypatch.setattr(probe, "EASTMONEY_DELAYS", ())
    fake = FakeAk()
    original = fake.index_realtime_sw
    fake.index_realtime_sw = lambda symbol: original(symbol) if symbol == "一级行业" else (_ for _ in ()).throw(KeyError("data"))
    monkeypatch.setattr(probe, "SW_DELAYS", ())
    results, exit_code = probe.run_selected_sources(fake, "all")
    assert exit_code == 0
    assert results["sw_l1"].status.status == "available"
    assert results["sw_l2"].status.status == "unavailable"
    assert "HTTP 507" in results["sw_l2"].status.last_error
    assert results["eastmoney"].status.status == "unavailable"
    assert results["eastmoney"].trends == ()


def test_sw_l1_failure_makes_all_fail(monkeypatch):
    fake = FakeAk()
    monkeypatch.setattr(fake, "index_realtime_sw", lambda symbol: (_ for _ in ()).throw(ConnectionError("sw down")))
    monkeypatch.setattr(probe, "SW_DELAYS", ())
    monkeypatch.setattr(probe, "EASTMONEY_DELAYS", ())
    _, exit_code = probe.run_selected_sources(fake, "all")
    assert exit_code == 1


def test_all_source_execution_order(monkeypatch):
    order = []
    unavailable = lambda source: probe.SourceResult(
        probe.SourceStatus(source, "unavailable", 0, 0, 0, "error", 0), ()
    )
    monkeypatch.setattr(probe, "run_source", lambda ak, source: order.append(source) or unavailable(source))
    probe.run_selected_sources(object(), "all")
    assert order == ["sw_l1", "sw_l2", "eastmoney"]
