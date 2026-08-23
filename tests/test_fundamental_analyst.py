from __future__ import annotations

import json

import httpx
import pytest

from backend.research.analyst_schema import validate_analyst
from backend.research.fundamental_analyst import AnalystInputError, FundamentalAnalyst
from backend.research.llm.base import GenerationResult, LlmProviderError
from backend.research.llm.deepseek import DeepSeekProvider
from backend.research.llm.factory import create_provider


def valid_payload(symbol='000858.SZ', version='2026-08-23_v1'):
    return {
        'schema_version': 1, 'symbol': symbol, 'analysis_version': version,
        'company_classification': {'primary_research_type': 'consumer_brand', 'secondary_research_type': 'high_dividend_cash_cow', 'business_model_summary': 'evidence-bound'},
        'investment_thesis': {'summary': 'unknown where evidence is insufficient', 'positive_drivers': [], 'market_concerns': []},
        'quality_assessment': {key: {'score': 5, 'reason': 'limited evidence'} for key in ('business_quality', 'earnings_quality', 'dividend_quality', 'governance_quality')},
        'normalized_earnings': {'unit': 'CNY_100M', 'period': 'FY2026E', 'bear': None, 'base': None, 'bull': None, 'method': 'insufficient_evidence', 'key_drivers': [], 'limitations': ['limited evidence']},
        'valuation': {'primary_method': 'insufficient_evidence', 'secondary_methods': [], 'bear_value_per_share': None, 'base_value_per_share': None, 'bull_value_per_share': None, 'methodology': 'limited evidence', 'limitations': ['limited evidence']},
        'claims': [{'claim_id': 'C001', 'statement': 'A filed report exists.', 'claim_type': 'FACT', 'confidence': 1, 'evidence_ids': ['financial_report:2025:annual_report'], 'reasoning': None}],
        'key_risks': [], 'provisional_view': {'summary': 'uncertain', 'valuation_attractiveness': 'uncertain', 'confidence': .5},
        'generation': {'provider': 'fake'},
    }


def test_schema_rejects_claim_semantic_errors():
    evidence = {'financial_report:2025:annual_report'}
    assert validate_analyst(valid_payload(), symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=evidence)
    for claim_type, expected in [('FACT', 'requires evidence_ids'), ('INFERENCE', 'requires reasoning'), ('ASSUMPTION', 'requires reasoning')]:
        payload = valid_payload(); payload['claims'][0].update(claim_type=claim_type, evidence_ids=[] if claim_type == 'FACT' else evidence, reasoning=None)
        with pytest.raises(ValueError, match=expected): validate_analyst(payload, symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=evidence)
    payload = valid_payload(); payload['claims'][0]['evidence_ids'] = ['unknown']
    with pytest.raises(ValueError, match='unknown evidence'): validate_analyst(payload, symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=evidence)
    payload = valid_payload(); payload['claims'][0]['confidence'] = 1.1
    with pytest.raises(Exception): validate_analyst(payload, symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=evidence)
    payload = valid_payload(); payload['normalized_earnings'].update(bear=3, base=2, bull=4)
    with pytest.raises(ValueError, match='bear <= base <= bull'): validate_analyst(payload, symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=evidence)
    payload = valid_payload(); payload['research_decision'] = 'add'
    with pytest.raises(Exception): validate_analyst(payload, symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=evidence)
    payload = valid_payload(); payload['claims'][0].update(statement='公司实施股份回购。', evidence_ids=['dividend_snapshot:2026-08-22'])
    with pytest.raises(ValueError, match='cannot use dividend_snapshot'):
        validate_analyst(payload, symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids={'dividend_snapshot:2026-08-22'})


def test_deepseek_provider_configuration_transport_and_usage(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    with pytest.raises(LlmProviderError, match='not configured'): create_provider('deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    assert isinstance(create_provider('deepseek'), DeepSeekProvider)
    def handler(request):
        assert request.headers['authorization'] == 'Bearer test-key'
        assert json.loads(request.content)['response_format'] == {'type': 'json_object'}
        assert json.loads(request.content)['thinking'] == {'type': 'disabled'}
        return httpx.Response(200, json={'model': 'deepseek-v4-flash', 'choices': [{'message': {'content': '{}'}}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})
    provider = DeepSeekProvider('test-key', client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.generate_structured(system_prompt='json', user_prompt='json')
    assert (result.provider, result.input_tokens, result.output_tokens) == ('deepseek', 12, 8)
    bad = DeepSeekProvider('test-key', client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))))
    with pytest.raises(LlmProviderError, match='request failed'): bad.generate_structured(system_prompt='json', user_prompt='json')
    class TimeoutClient:
        def post(self, *_args, **_kwargs): raise httpx.TimeoutException('timeout')
    with pytest.raises(LlmProviderError, match='timed out'):
        DeepSeekProvider('test-key', client=TimeoutClient()).generate_structured(system_prompt='json', user_prompt='json')


class FakeProvider:
    def __init__(self, text, fail=False): self.text, self.fail = text, fail
    def generate_structured(self, **_kwargs):
        if self.fail: raise LlmProviderError('fake transport failure')
        return GenerationResult(self.text, 'fake', 'fake-model', 1, 2, 3)


def write_pack(root, version, *, analyst=False):
    path = root/'000858.SZ'/'analysis'/version; path.mkdir(parents=True)
    (path/'input_snapshot.json').write_text(json.dumps({'symbol': '000858.SZ', 'name': '五粮液'}), encoding='utf-8')
    (path/'evidence.json').write_text(json.dumps({'symbol': '000858.SZ', 'evidence': [{'id': 'financial_report:2025:annual_report', 'type': 'financial_report', 'file': '../../sources/a.pdf'}]}), encoding='utf-8')
    if analyst: (path/'analyst.json').write_text('{}', encoding='utf-8')
    return path


def test_runner_version_selection_atomic_output_and_failures(tmp_path, monkeypatch):
    old = write_pack(tmp_path, '2026-08-23_v1', analyst=True)
    current = write_pack(tmp_path, '2026-08-23_v2')
    monkeypatch.setattr('backend.research.fundamental_analyst.build_context', lambda *_args: ('[SOURCE: financial_report:2025:annual_report] text [/SOURCE]', {'financial_report:2025:annual_report'}))
    runner = FundamentalAnalyst(provider=FakeProvider(json.dumps(valid_payload(version='2026-08-23_v2'))), research_root=tmp_path)
    result = runner.run('000858.SZ')
    assert result['version'] == '2026-08-23_v2' and (current/'analyst.json').exists() and not (old/'analyst.json').read_text() == ''
    with pytest.raises(FileExistsError): runner.run('000858.SZ', version='2026-08-23_v2')
    explicit = runner.run('000858.SZ', version='2026-08-23_v2', dry_run=True)
    assert explicit['dry_run'] is True
    failed_dir = write_pack(tmp_path, '2026-08-24_v1')
    failed = FundamentalAnalyst(provider=FakeProvider('{}', fail=True), research_root=tmp_path)
    with pytest.raises(LlmProviderError): failed.run('000858.SZ', version='2026-08-24_v1')
    assert not (failed_dir/'analyst.json').exists()
    invalid_dir = write_pack(tmp_path, '2026-08-25_v1')
    invalid = FundamentalAnalyst(provider=FakeProvider(json.dumps({'bad': True})), research_root=tmp_path)
    with pytest.raises(Exception): invalid.run('000858.SZ', version='2026-08-25_v1')
    assert not (invalid_dir/'analyst.json').exists()


def test_runner_rejects_pack_without_readable_evidence(tmp_path):
    write_pack(tmp_path, '2026-08-23_v1')
    with pytest.raises(AnalystInputError, match='no readable evidence'):
        FundamentalAnalyst(research_root=tmp_path).prepare('000858.SZ')
