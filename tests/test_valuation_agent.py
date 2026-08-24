from __future__ import annotations
import json
import pandas as pd
import pytest
from pathlib import Path
from backend.research.valuation_input_builder import ValuationInputBuilder
from backend.research.json_store import read_json, write_json_atomic
from backend.research.llm.base import GenerationResult, LlmProviderError
from backend.research.valuation_agent import ValuationAgent
from backend.research.valuation_metrics import fcf,percentile,ratio
from backend.research.valuation_schema import validate_valuation

def payload():
 return {'schema_version':1,'symbol':'000858.SZ','analysis_version':'v1','generation':{},'normalized_earnings':{'period':'FY2026E','unit':'CNY_100M','bear':1,'base':2,'bull':3,'method':'history','key_assumptions':[],'assumption_metadata':[{'evidence_ids':['financial_report:2025:annual_report'],'historical_anchor':'history','latest_period_anchor':'latest','risk_adjustment':'risk','method':'method','limitations':'limits'}],'evidence_ids':['financial_report:2025:annual_report'],'forecast_quality':'LOW','confidence':.5},'valuation_method':{'primary':'PE','secondary':[],'reasoning':'x','multiple_source':'assumption'},'multiples':{'bear':10,'base':12,'bull':15,'metric':'PE','assumption_rationale':['historical range']},'fair_value_per_share':{'bear':1,'base':2,'bull':3},'reverse_valuation':{'current_price':2,'current_market_cap':20,'implied_normalized_earnings':2,'implied_multiple':12,'scenarios':[{'scenario':'base','multiple':10,'implied_earnings':2,'interpretation':'x'}], 'interpretation':'x'},'dividend_cross_check':{'current_yield':.05,'sustainable_dividend':None,'sustainable_yield':None,'assessment':'uncertain'},'limitations':[],'confidence':.5}
def test_metrics_and_schema():
 assert fcf(10,3)==7 and fcf(10,None) is None and ratio(2,4)==.5 and ratio(2,0) is None
 assert percentile([1,-1,3],2)=={'percentile':.5,'sample_count':2,'method':'positive_only'}
 assert validate_valuation(payload(),symbol='000858.SZ',version='v1',evidence_ids={'financial_report:2025:annual_report'})
 bad=payload();bad['fair_value_per_share']['base']=0
 with pytest.raises(ValueError,match='bear <= base <= bull'):validate_valuation(bad,symbol='000858.SZ',version='v1',evidence_ids={'financial_report:2025:annual_report'})
 bad=payload();bad['normalized_earnings']['evidence_ids']=[]
 with pytest.raises(ValueError,match='requires evidence'):validate_valuation(bad,symbol='000858.SZ',version='v1',evidence_ids={'financial_report:2025:annual_report'})
 bad=payload();bad['research_decision']='add'
 with pytest.raises(Exception):validate_valuation(bad,symbol='000858.SZ',version='v1',evidence_ids={'financial_report:2025:annual_report'})
 bad=payload();bad['normalized_earnings']['forecast_quality']='INSUFFICIENT'
 with pytest.raises(ValueError,match='cannot be INSUFFICIENT'):validate_valuation(bad,symbol='000858.SZ',version='v1',evidence_ids={'financial_report:2025:annual_report'})
 bad=payload();bad['dividend_cross_check']['current_yield']=1.01
 with pytest.raises(ValueError):validate_valuation(bad,symbol='000858.SZ',version='v1',evidence_ids={'financial_report:2025:annual_report'})


class FakeProvider:
 def data(self, _symbol):
  return {
   'income': pd.DataFrame([
    {'end_date':'20241231','ann_date':'20250301','total_revenue':100,'n_income_attr_p':10,'profit_dedt':9,'basic_eps':1},
    {'end_date':'20231231','ann_date':'20240301','total_revenue':90,'n_income_attr_p':-2,'profit_dedt':-3,'basic_eps':-0.2},
   ]),
   'cashflow': pd.DataFrame([
    {'end_date':'20241231','ann_date':'20250301','n_cashflow_act':20,'c_pay_acq_const_fiolta':5},
    {'end_date':'20231231','ann_date':'20240301','n_cashflow_act':8,'c_pay_acq_const_fiolta':None},
   ]),
   'balance': pd.DataFrame([
    {'end_date':'20251231','ann_date':'20260301','money_cap':50,'st_borr':10,'lt_borr':5,'bonds_pay':None,'non_cur_liab_due_1y':2,'total_hldr_eqy_exc_min_int':80,'total_share':100},
    {'end_date':'20241231','ann_date':'20250301','money_cap':50,'st_borr':10,'lt_borr':5,'bonds_pay':None,'non_cur_liab_due_1y':2,'total_hldr_eqy_exc_min_int':80,'total_share':100},
   ]),
   'indicator': pd.DataFrame([{'end_date':'20241231','ann_date':'20250301','roe':12}]),
   'daily_basic': pd.DataFrame([
    {'trade_date':'20250801','close':10,'pe_ttm':20,'pb':2,'total_mv':100},
    {'trade_date':'20250731','close':9,'pe_ttm':-1,'pb':1,'total_mv':90},
   ]),
  }


def test_input_builder_derives_values_and_records_missing_data(tmp_path: Path):
 root = tmp_path / 'research'; folder = root / '000858.SZ' / 'analysis' / 'v1'
 folder.mkdir(parents=True)
 write_json_atomic(root / '000858.SZ' / 'company.json', {'research_type': {'primary': 'consumer_brand'}})
 write_json_atomic(folder / 'evidence.json', {'evidence': [{'id': 'dividend_snapshot:2026-08-23'}]})
 write_json_atomic(folder / 'input_snapshot.json', {
  'symbol': '000858.SZ', 'as_of': '2026-08-23', 'yield_context': {'latest_price': 10},
  'dividend_history': [{'calendar_year': 2024, 'current_basis_dps': 1}, {'calendar_year': 2023, 'current_basis_dps': 1}],
 })
 result = ValuationInputBuilder(provider=FakeProvider(), research_root=root).build('000858.SZ', version='v1')
 value = read_json(Path(result['path']))
 y2024 = next(row for row in value['earnings_history'] if row['period'] == '2024')
 y2023 = next(row for row in value['earnings_history'] if row['period'] == '2023')
 assert y2024['free_cash_flow']['value'] == 15
 assert y2024['payout_ratio']['value'] == 10
 assert y2024['dividend_to_fcf_ratio']['value'] == pytest.approx(100 / 15)
 assert y2023['payout_ratio']['value'] is None
 assert y2023['free_cash_flow']['value'] is None
 assert value['balance_sheet']['net_cash']['value'] == 33
 assert value['valuation_history']['pe'] == {'percentile': 1.0, 'sample_count': 1, 'method': 'positive_only'}
 assert value['forward_consensus']['status'] == 'UNAVAILABLE'
 assert any('2023 capex unavailable' == item for item in value['missing_data'])
 assert value['balance_sheet']['net_cash']['formula'] == 'cash_minus_interest_bearing_debt'


def test_input_builder_includes_existing_forward_consensus(tmp_path: Path):
 root = tmp_path / 'research'; folder = root / '000858.SZ' / 'analysis' / 'v1'; folder.mkdir(parents=True)
 write_json_atomic(root / '000858.SZ' / 'company.json', {'research_type': {}})
 write_json_atomic(folder / 'evidence.json', {'evidence': []})
 write_json_atomic(folder / 'input_snapshot.json', {'symbol': '000858.SZ', 'as_of': '2026-08-23', 'dividend_history': []})
 write_json_atomic(folder / 'forward_consensus.json', {'status': 'AVAILABLE', 'forecast_years': {'2026': {'quality': 'LOW'}}})
 ValuationInputBuilder(provider=FakeProvider(), research_root=root).build('000858.SZ', version='v1')
 assert read_json(folder / 'valuation_input.json')['forward_consensus']['forecast_years']['2026']['quality'] == 'LOW'


def test_input_builder_dry_run_does_not_write(tmp_path: Path):
 root = tmp_path / 'research'; folder = root / '000858.SZ' / 'analysis' / 'v1'
 folder.mkdir(parents=True)
 write_json_atomic(root / '000858.SZ' / 'company.json', {'research_type': {}})
 write_json_atomic(folder / 'evidence.json', {'evidence': []})
 write_json_atomic(folder / 'input_snapshot.json', {'symbol': '000858.SZ', 'dividend_history': []})
 result = ValuationInputBuilder(provider=FakeProvider(), research_root=root).build('000858.SZ', version='v1', dry_run=True)
 assert result['dry_run'] is True
 assert not (folder / 'valuation_input.json').exists()


class FakeLlm:
 def __init__(self, text, fail=False): self.text, self.fail = text, fail
 def generate_structured(self, **_kwargs):
  if self.fail: raise LlmProviderError('fake failure')
  return GenerationResult(self.text, 'fake', 'fake-model', 1, 2, 3)


def test_valuation_runner_selects_version_and_does_not_write_on_failure(tmp_path: Path, monkeypatch):
 folder = tmp_path / '000858.SZ' / 'analysis' / 'v1'; folder.mkdir(parents=True)
 write_json_atomic(tmp_path / '000858.SZ' / 'company.json', {'symbol': '000858.SZ'})
 write_json_atomic(folder / 'input_snapshot.json', {'symbol': '000858.SZ'})
 write_json_atomic(folder / 'evidence.json', {'evidence': [{'id': 'financial_report:2025:annual_report'}]})
 write_json_atomic(folder / 'analyst.json', {'claims': []})
 write_json_atomic(folder / 'skeptic.json', {'verdict': 'CAUTION'})
 write_json_atomic(folder / 'valuation_input.json', {'symbol': '000858.SZ'})
 monkeypatch.setattr('backend.research.valuation_agent.build_context', lambda *_args: ('context', {'financial_report:2025:annual_report'}))
 runner = ValuationAgent(provider=FakeLlm(json.dumps(payload())), research_root=tmp_path)
 assert runner.run('000858.SZ')['version'] == 'v1'
 assert (folder / 'valuation.json').exists()
 with pytest.raises(FileExistsError): runner.run('000858.SZ', version='v1')
 assert runner.run('000858.SZ', version='v1', dry_run=True)['dry_run']
 (folder / 'valuation.json').unlink()
 with pytest.raises(LlmProviderError): ValuationAgent(provider=FakeLlm('{}', fail=True), research_root=tmp_path).run('000858.SZ', version='v1')
 assert not (folder / 'valuation.json').exists()
