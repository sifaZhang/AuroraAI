from __future__ import annotations

import json
import sqlite3

import pytest

from backend.research.announcement_collector import classify_announcement
from backend.research.financial_report_collector import choose_initial_reports, classify_financial_report
from backend.research.input_pack_builder import InputPackBuilder
from backend.research.profile_builder import ProfileBuilder
from backend.research.profile_repository import ProfileRepository
from backend.research.source_collector import CNINFO_QUERY_URL, CNINFO_SEARCH_URL, CninfoClient, discover_documents, save_document
from backend.research.source_metadata import SourceDocument


class FakeClient:
    def __init__(self, rows, content=b"%PDF-1.7\nfixture"):
        self.rows, self.content, self.downloads = rows, content, 0
    def discover(self, symbol): return self.rows
    def download(self, url): self.downloads += 1; return self.content


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class FakeHttp:
    def __init__(self): self.calls = []
    def post(self, url, data):
        self.calls.append((url, data))
        if url == CNINFO_SEARCH_URL:
            return FakeResponse([{'code': '000858', 'orgId': 'gssz0000858'}])
        return FakeResponse({'announcements': [
            {'announcementTitle': '2025年年度报告', 'adjunctUrl': f'final/{data["category"] or "all"}.pdf'}
        ]})


def _connection():
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE dividend_stable_universe(market TEXT,symbol TEXT,company_name TEXT,is_enabled INTEGER,grade TEXT,entry_yield REAL,add_yield REAL,heavy_yield REAL);
    CREATE TABLE dividend_yield_snapshots(market TEXT,symbol TEXT,calculation_date TEXT,latest_price REAL,three_year_average_dps REAL,three_year_average_yield REAL,price_date TEXT);
    CREATE TABLE industry_memberships_current(classification TEXT,classification_version TEXT,symbol TEXT,level1_code TEXT,level1_name TEXT,level2_code TEXT,level2_name TEXT,level3_code TEXT,level3_name TEXT);
    CREATE TABLE annual_cash_dividend_summaries(symbol TEXT,calendar_year INTEGER,cash_dividend_per_share REAL,current_basis_dps REAL,share_basis_as_of TEXT);
    INSERT INTO dividend_stable_universe VALUES('CN','000858.SZ','五粮液',1,'A',5,6,7);
    INSERT INTO dividend_yield_snapshots VALUES('CN','000858.SZ','2026-08-21',71.19,5.19,.0729,'2026-08-21');
    INSERT INTO industry_memberships_current VALUES('SW','2021','000858.SZ','1','食品饮料','2','白酒Ⅱ','3','白酒Ⅲ');
    INSERT INTO annual_cash_dividend_summaries VALUES('000858.SZ',2023,4,4,'2026-08-21');
    """)
    return con


def test_financial_and_announcement_classification_is_conservative():
    document = classify_financial_report('五粮液：2025年年度报告', '000858.SZ', 'url', '2026-04-01', '1')
    assert document and (document.document_type, document.period) == ('annual_report', '2025')
    half_year = classify_financial_report('五粮液：2026年半年度报告', '000858.SZ', 'url', '2026-08-01', '2')
    assert half_year and (half_year.document_type, half_year.period) == ('half_year_report', '2026H1')
    assert classify_financial_report('五粮液：2025年年度报告摘要', '000858.SZ', 'url', None, None) is None
    assert classify_announcement('2026年度利润分配预案') == 'dividend'
    assert classify_announcement('关于回购公司股份的进展公告') == 'share_repurchase'
    assert classify_announcement('董事会会议决议公告') is None


def test_initial_reports_use_completed_annual_and_latest_periodic():
    docs = [
        SourceDocument('000858.SZ', 'annual', 'a', None, 'a', 'annual_report', period='2025'),
        SourceDocument('000858.SZ', 'q1', 'b', None, 'b', 'q1_report', period='2026Q1'),
        SourceDocument('000858.SZ', 'h1', 'c', None, 'c', 'half_year_report', period='2026H1'),
    ]
    assert [(x.document_type, x.period) for x in choose_initial_reports(docs)] == [
        ('annual_report', '2025'), ('half_year_report', '2026H1')
    ]


def test_discovery_deduplicates_periods_and_save_validates_pdf(tmp_path):
    rows = [
      {'announcementTitle':'2025年年度报告','adjunctUrl':'final/a.pdf','announcementTime':1770000000000,'announcementId':'1'},
      {'announcementTitle':'2025年年度报告','adjunctUrl':'final/b.pdf','announcementTime':1770000000000,'announcementId':'2'},
      {'announcementTitle':'关于利润分配方案的公告','adjunctUrl':'final/c.pdf','announcementTime':1770000000000,'announcementId':'3'},
    ]
    client = FakeClient(rows); reports, announcements = discover_documents('000858.SZ', client)
    assert len(reports) == len(announcements) == 1
    root = tmp_path/'000858.SZ'; (root/'sources'/'financial_reports').mkdir(parents=True); (root/'sources'/'announcements').mkdir(parents=True)
    assert save_document(reports[0], root, client) == 'downloaded'
    assert save_document(reports[0], root, client) == 'skipped' and client.downloads == 1
    metadata = list((root/'sources'/'financial_reports').glob('*.meta.json'))[0]
    assert json.loads(metadata.read_text(encoding='utf-8'))['sha256']
    invalid = SourceDocument('000858.SZ', 'invalid', 'https://static.cninfo.com.cn/final/invalid.pdf', None, 'invalid', 'announcement', announcement_type='dividend')
    with pytest.raises(ValueError): save_document(invalid, root, FakeClient(rows, b'<html>not pdf</html>'))


def test_cninfo_client_uses_org_id_and_regular_report_categories():
    http = FakeHttp()
    rows = CninfoClient(http, retries=0).discover('000858.SZ')
    queries = [data for url, data in http.calls if url == CNINFO_QUERY_URL]
    assert len(queries) == 5
    assert all(query['stock'] == '000858,gssz0000858' for query in queries)
    assert {query['category'] for query in queries} == {'', 'category_ndbg_szsh', 'category_yjdbg_szsh', 'category_bndbg_szsh', 'category_sjdbg_szsh'}
    assert len(rows) == 5


def test_input_pack_uses_db_snapshot_and_source_metadata_without_writes(tmp_path):
    root = tmp_path/'research'; mapping = tmp_path/'mapping.yaml'
    mapping.write_text('by_sw_l3:\n  白酒Ⅲ:\n    primary: consumer_brand\noverrides:\n', encoding='utf-8')
    repository = ProfileRepository(_connection()); ProfileBuilder(repository, research_root=root, mapping_path=mapping).build('000858.SZ')
    folder = root/'000858.SZ'/'sources'/'financial_reports'; folder.mkdir(parents=True, exist_ok=True)
    (folder/'2025_annual.pdf').write_bytes(b'%PDF-x')
    (folder/'2025_annual.meta.json').write_text(json.dumps({'document_type':'annual_report','period':'2025','file_name':'2025_annual.pdf','source':'cninfo','sha256':'abc'}), encoding='utf-8')
    result = InputPackBuilder(repository, research_root=root).build('000858.SZ', as_of='2026-08-23')
    snapshot = json.loads((root/'000858.SZ'/'analysis'/result['version']/'input_snapshot.json').read_text(encoding='utf-8'))
    assert snapshot['yield_context']['latest_price'] == 71.19 and snapshot['yield_context']['yield_signal'] == 'heavy'
    assert snapshot['dividend_history'][0]['calendar_year'] == 2023 and snapshot['financial_reports'][0]['sha256'] == 'abc'


def test_collector_module_has_cli_entrypoint():
    from backend.research import source_collector
    assert source_collector.main
    assert 'raise SystemExit(main())' in open(source_collector.__file__, encoding='utf-8').read()
