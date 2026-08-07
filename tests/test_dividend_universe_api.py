from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api import dividend_universe as universe_api
from backend.api.app import app


class SharedConnection:
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def close(self):
        pass

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *args):
        return self.connection.__exit__(*args)


def _database():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
    CREATE TABLE a_share_security_master(symbol TEXT PRIMARY KEY, security_name TEXT, listed_date TEXT, is_active INTEGER, delisted_date TEXT, exchange TEXT);
    CREATE TABLE a_share_security_status_history(symbol TEXT, effective_date TEXT, is_st INTEGER);
    CREATE TABLE industry_memberships_current(symbol TEXT PRIMARY KEY, level1_name TEXT, level2_name TEXT, level3_name TEXT, source TEXT);
    CREATE TABLE dividend_stable_universe (market TEXT, symbol TEXT, company_name TEXT, industry_level_1 TEXT, industry_level_2 TEXT, monopoly_type TEXT, stability_subtype TEXT, inclusion_source TEXT CHECK(inclusion_source IN ('automatic_rule','manual_addition','manual_review')), inclusion_reason TEXT, risk_note TEXT, is_enabled INTEGER DEFAULT 1, included_at TEXT, updated_at TEXT, PRIMARY KEY(market,symbol));
    CREATE TABLE annual_cash_dividend_summaries (market TEXT, symbol TEXT, calendar_year INTEGER, cash_dividend_per_share REAL, dividend_event_count INTEGER, calculation_method TEXT, source TEXT, data_quality_status TEXT, calculated_at TEXT, updated_at TEXT, PRIMARY KEY(market,symbol,calendar_year));
    INSERT INTO a_share_security_master VALUES ('600001.SH','Test Hydro','2010-01-01',1,NULL,'SH');
    INSERT INTO a_share_security_master VALUES ('200001.SZ','B Share','2010-01-01',1,NULL,'SZ');
    INSERT INTO a_share_security_status_history VALUES ('600001.SH','2026-01-01',0);
    INSERT INTO a_share_security_status_history VALUES ('200001.SZ','2026-01-01',0);
    INSERT INTO industry_memberships_current VALUES ('600001.SH','Utilities','Hydropower','Hydropower','test');
    INSERT INTO industry_memberships_current VALUES ('200001.SZ','Utilities','Hydropower','Hydropower','test');
    INSERT INTO dividend_stable_universe VALUES ('CN','600001.SH','Test Hydro','Utilities','Hydropower','hydropower_resource','stable_monopoly','automatic_rule','rule','',1,'2026-01-01','2026-01-01');
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600001.SH',2023,0.1,1,'method','test','complete','2026-01-01','2026-01-01');
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600001.SH',2024,0.2,2,'method','test','complete','2026-01-01','2026-01-01');
    INSERT INTO annual_cash_dividend_summaries VALUES ('CN','600001.SH',2025,0.3,1,'method','test','complete','2026-01-01','2026-01-01');
    """)
    return connection


def _client(monkeypatch):
    connection = _database()
    monkeypatch.setattr(universe_api, "connect", lambda: SharedConnection(connection))
    monkeypatch.setattr(universe_api, "migrate", lambda _connection: None)
    return connection, TestClient(app)


def test_list_search_and_status_changes_preserve_dps(monkeypatch):
    connection, client = _client(monkeypatch)
    response = client.get('/api/dividend/universe')
    assert response.status_code == 200
    payload = response.json()
    assert payload['target_years'] == [2023, 2024, 2025]
    assert payload['items'][0]['annual_dps'] == {'2023': 0.1, '2024': 0.2, '2025': 0.3}
    assert payload['items'][0]['three_year_average_dps'] == 0.2
    assert client.get('/api/dividend/universe/search?q=Test').json()['items'][0]['symbol'] == '600001.SH'
    assert client.get('/api/dividend/universe/search?q=B%20Share').json()['items'] == []
    assert client.patch('/api/dividend/universe/600001.SH/status', json={'is_enabled': False}).status_code == 200
    assert connection.execute("SELECT COUNT(*) FROM annual_cash_dividend_summaries").fetchone()[0] == 3
    assert client.patch('/api/dividend/universe/600001.SH/status', json={'is_enabled': True}).status_code == 200


def test_manual_add_requires_acknowledgement_and_uses_actual_event_counts(monkeypatch):
    connection, client = _client(monkeypatch)
    monkeypatch.setattr(universe_api, '_validate', lambda *_args: {
        'symbol': '600001.SH', 'company_name': 'Test Hydro', 'can_add': True, 'warnings': ['manual review'],
        'annual_dps': {'2023': 0.1, '2024': 0.2, '2025': 0.3},
        'dividend_event_counts': {'2023': 1, '2024': 2, '2025': 1},
    })
    connection.execute("DELETE FROM annual_cash_dividend_summaries")
    connection.execute("DELETE FROM dividend_stable_universe")
    request = {'symbol': '600001.SH', 'stability_subtype': 'stable_monopoly', 'monopoly_type': 'hydropower_resource', 'manual_reason': 'reviewed'}
    assert client.post('/api/dividend/universe', json=request).status_code == 422
    response = client.post('/api/dividend/universe', json={**request, 'acknowledge_warnings': True})
    assert response.status_code == 200 and response.json()['status'] == 'added'
    assert connection.execute("SELECT inclusion_source FROM dividend_stable_universe WHERE symbol='600001.SH'").fetchone()[0] == 'manual_review'
    assert connection.execute("SELECT dividend_event_count FROM annual_cash_dividend_summaries WHERE calendar_year=2024").fetchone()[0] == 2
    assert client.post('/api/dividend/universe', json={**request, 'acknowledge_warnings': True}).json()['status'] == 'already_exists'
    assert client.post('/api/dividend/universe/validate', json={'symbol': '200001.SZ'}).status_code == 422


def test_universe_page_and_navigation_are_registered():
    client = TestClient(app)
    page = client.get('/dividend/universe')
    assert page.status_code == 200
    assert 'src="/dividend-universe.js"' in page.text
    assert 'href="/styles.css"' in page.text
    assert 'dividend/universe' in client.get('/').text


def test_frontend_distinguishes_loading_empty_and_error_states():
    script = (Path(__file__).parents[1] / 'frontend' / 'dividend-universe.js').read_text(encoding='utf-8')
    page = (Path(__file__).parents[1] / 'frontend' / 'dividend-universe.html').read_text(encoding='utf-8')
    assert 'displayError(error)' in script
    assert "$('empty-state').hidden = false" in script
    assert "data.items.length === 1" in script
    assert "$('add-monopoly').value = ''" in script
    assert 'id="load-error"' in page
    assert 'id="retry"' in page
