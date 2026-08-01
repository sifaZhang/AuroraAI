from datetime import date

import pandas as pd
import pytest

from backend.data_sources.errors import ProviderEmptyDataError, ProviderSchemaError
from backend.data_sources.tushare.industry_provider import TushareIndustryProvider


class Client:
    def __init__(self, frame=None, error=None): self.frame, self.error, self.calls = frame, error, []
    def call(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        if self.error: raise self.error
        return self.frame


def test_trade_cal_maps_open_closed_days_and_makes_one_range_call():
    client = Client(pd.DataFrame({"cal_date":["20260731","20260801"], "is_open":[1,0], "pretrade_date":["20260730","20260731"]}))
    result = TushareIndustryProvider(client).list_calendar_days(start_date=date(2026,7,31), end_date=date(2026,8,1))
    assert [item.is_open for item in result.data] == [True, False]
    assert result.data[0].trade_date == date(2026,7,31)
    assert client.calls == [("trade_cal", {"exchange":"SSE", "start_date":"20260731", "end_date":"20260801", "fields":"exchange,cal_date,is_open,pretrade_date"})]


@pytest.mark.parametrize("frame,error", [(pd.DataFrame(), None), (pd.DataFrame({"cal_date":[]}), None)])
def test_trade_cal_empty_or_schema_is_not_treated_as_market_closed(frame, error):
    provider = TushareIndustryProvider(Client(frame, error))
    expected = ProviderEmptyDataError if frame.empty else ProviderSchemaError
    with pytest.raises(expected): provider.list_calendar_days(start_date=date(2026,1,1), end_date=date(2026,1,2))
