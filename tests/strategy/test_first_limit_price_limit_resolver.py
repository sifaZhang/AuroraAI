from datetime import date
from decimal import Decimal
from backend.strategy.first_limit.contracts import BoardType,DataSource,SecurityStatus
from backend.strategy.first_limit.rules import normalize_symbol,resolve_limit_prices,resolve_price_limit_rule
from backend.strategy.first_limit.tushare_price_limits import load_price_limits

def rule(): return resolve_price_limit_rule('600000.SH',date(2026,1,2),SecurityStatus(normalize_symbol('600000.SH'),date(2026,1,2),BoardType.MAIN,source=DataSource.GM))
def test_tushare_priority_and_fallbacks():
 assert resolve_limit_prices('10',rule(),source_upper_limit='11',source_lower_limit='9',tushare_upper_limit='11.1',tushare_lower_limit='9').selection_basis=='tushare_stk_limit'
 assert resolve_limit_prices('10',rule(),source_upper_limit='11',source_lower_limit='9').selection_basis=='source_authoritative'
 assert resolve_limit_prices('10',rule()).selection_basis=='calculated_fallback'
def test_day_failure_isolated():
 class C:
  def call(self,*_,**k):
   if k['trade_date']=='20260102': raise RuntimeError()
   return [{'ts_code':'600000.SH','trade_date':'20260101','pre_close':10,'up_limit':11,'down_limit':9}]
 values,failures=load_price_limits(C(),[date(2026,1,1),date(2026,1,2)])
 assert ('600000.SH',date(2026,1,1)) in values and date(2026,1,2) in failures
