from __future__ import annotations

import json

import pytest

from backend.research.llm.base import GenerationResult, LlmProviderError
from backend.research.fundamental_analyst import AnalystInputError
from backend.research.skeptic_analyst import SkepticAnalyst
from backend.research.skeptic_schema import validate_skeptic


EVIDENCE = {'financial_report:2025:annual_report'}
CLAIMS = {'C001'}


def payload(*, verdict='CAUTION', cap='ENTRY'):
    return {
        'schema_version': 1, 'symbol': '000858.SZ', 'analysis_version': '2026-08-23_v1', 'generation': {'provider': 'fake'},
        'verdict': verdict, 'overall_assessment': {'summary': 'caution', 'confidence': .6},
        'challenged_claims': [{'analyst_claim_id': 'C001', 'status': 'WEAKLY_SUPPORTED', 'severity': 'HIGH', 'reason': 'single period', 'evidence_ids': ['financial_report:2025:annual_report']}],
        'major_risks': [{'risk_id': 'R001', 'category': 'cash_flow', 'severity': 'HIGH', 'statement': 'cash flow risk', 'evidence_ids': ['financial_report:2025:annual_report'], 'reasoning': 'reported variability', 'invalidate_condition': 'sustained cash conversion'}],
        'dividend_trap_assessment': {'status': 'POSSIBLE', 'confidence': .5, 'reasons': ['coverage uncertain'], 'supporting_evidence_ids': ['financial_report:2025:annual_report'], 'missing_evidence': ['FCF']},
        'earnings_risk_assessment': {'summary': 'single-period risk', 'confidence': .5, 'challenged_assumptions': ['C001'], 'evidence_ids': ['financial_report:2025:annual_report']},
        'governance_risk_assessment': {'summary': 'unknown', 'confidence': .2, 'evidence_ids': [], 'missing_evidence': ['governance evidence']},
        'missing_evidence': [{'item': 'H1 report', 'importance': 'HIGH', 'reason': 'needed'}], 'recommended_research_cap': cap,
    }


def test_skeptic_schema_semantics():
    assert validate_skeptic(payload(), symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=EVIDENCE, analyst_claim_ids=CLAIMS)
    for verdict, cap, error in [('BLOCK', 'ENTRY', 'requires WATCH'), ('CAUTION', 'HEAVY', 'cannot have HEAVY')]:
        with pytest.raises(ValueError, match=error): validate_skeptic(payload(verdict=verdict, cap=cap), symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=EVIDENCE, analyst_claim_ids=CLAIMS)
    item = payload(); item['challenged_claims'][0]['analyst_claim_id'] = 'C404'
    with pytest.raises(ValueError, match='does not exist'): validate_skeptic(item, symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=EVIDENCE, analyst_claim_ids=CLAIMS)
    item = payload(); item['major_risks'][0]['evidence_ids'] = ['unknown']
    with pytest.raises(ValueError, match='unknown evidence'): validate_skeptic(item, symbol='000858.SZ', analysis_version='2026-08-23_v1', evidence_ids=EVIDENCE, analyst_claim_ids=CLAIMS)


class FakeProvider:
    def __init__(self, text, fail=False): self.text, self.fail = text, fail
    def generate_structured(self, **_kwargs):
        if self.fail: raise LlmProviderError('fake failure')
        return GenerationResult(self.text, 'fake', 'fake-model', 1, 2, 3)


def write_pack(root, version, *, skeptic=False, analyst=True):
    folder = root/'000858.SZ'/'analysis'/version; folder.mkdir(parents=True)
    (root/'000858.SZ'/'company.json').write_text(json.dumps({'symbol': '000858.SZ'}), encoding='utf-8')
    (folder/'input_snapshot.json').write_text(json.dumps({'symbol': '000858.SZ'}), encoding='utf-8')
    (folder/'evidence.json').write_text(json.dumps({'symbol': '000858.SZ', 'evidence': [{'id': 'financial_report:2025:annual_report', 'type': 'financial_report', 'file': '../../sources/a.pdf'}]}), encoding='utf-8')
    if analyst: (folder/'analyst.json').write_text(json.dumps({'symbol': '000858.SZ', 'claims': [{'claim_id': 'C001'}]}), encoding='utf-8')
    if skeptic: (folder/'skeptic.json').write_text('{}', encoding='utf-8')
    return folder


def test_runner_selection_write_force_and_failures(tmp_path, monkeypatch):
    old = write_pack(tmp_path, '2026-08-23_v1', skeptic=True)
    current = write_pack(tmp_path, '2026-08-23_v2')
    monkeypatch.setattr('backend.research.skeptic_analyst.build_context', lambda *_args: ('[SOURCE: financial_report:2025:annual_report] x [/SOURCE]', EVIDENCE))
    runner = SkepticAnalyst(provider=FakeProvider(json.dumps(payload() | {'analysis_version': '2026-08-23_v2'})), research_root=tmp_path)
    assert runner.run('000858.SZ')['version'] == '2026-08-23_v2'
    assert (current/'skeptic.json').exists()
    with pytest.raises(FileExistsError): runner.run('000858.SZ', version='2026-08-23_v2')
    assert runner.run('000858.SZ', version='2026-08-23_v2', dry_run=True)['dry_run']
    invalid = write_pack(tmp_path, '2026-08-24_v1')
    bad = SkepticAnalyst(provider=FakeProvider('{}', fail=True), research_root=tmp_path)
    with pytest.raises(LlmProviderError): bad.run('000858.SZ', version='2026-08-24_v1')
    assert not (invalid/'skeptic.json').exists()
    no_analyst = write_pack(tmp_path, '2026-08-25_v1', analyst=False)
    with pytest.raises(AnalystInputError): SkepticAnalyst(research_root=tmp_path).prepare('000858.SZ', '2026-08-25_v1')
    assert old.exists()
