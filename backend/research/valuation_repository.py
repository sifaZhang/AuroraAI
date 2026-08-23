from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import tushare as ts


class ValuationDataError(RuntimeError):
    pass


@dataclass
class TushareValuationProvider:
    token: str | None = None
    pro: Any = None

    def __post_init__(self) -> None:
        self.token = self.token or os.environ.get("TUSHARE_TOKEN")
        if self.pro is None:
            if not self.token:
                raise ValuationDataError("Tushare token is not configured")
            self.pro = ts.pro_api(self.token)

    def data(self, symbol: str) -> dict[str, pd.DataFrame]:
        code = symbol.upper()
        start = f"{date.today().year - 5}0101"
        try:
            return {
                "income": self.pro.income(ts_code=code, start_date=start, fields="ts_code,end_date,ann_date,total_revenue,n_income_attr_p,profit_dedt,basic_eps"),
                "cashflow": self.pro.cashflow(ts_code=code, start_date=start, fields="ts_code,end_date,ann_date,n_cashflow_act,c_pay_acq_const_fiolta"),
                "balance": self.pro.balancesheet(ts_code=code, start_date=start, fields="ts_code,end_date,ann_date,money_cap,st_borr,lt_borr,bonds_pay,non_cur_liab_due_1y,total_hldr_eqy_exc_min_int,total_share"),
                "indicator": self.pro.fina_indicator(ts_code=code, start_date=start, fields="ts_code,end_date,ann_date,roe"),
                "daily_basic": self.pro.daily_basic(ts_code=code, start_date=start, fields="ts_code,trade_date,close,pe_ttm,pb,total_mv"),
            }
        except Exception as exc:
            raise ValuationDataError(f"Tushare valuation data request failed: {type(exc).__name__}") from exc
