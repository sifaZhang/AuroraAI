from __future__ import annotations

import sqlite3
import csv
import json
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
    CREATE TABLE dividend_stable_universe (market TEXT, symbol TEXT, company_name TEXT, industry_level_1 TEXT, industry_level_2 TEXT, monopoly_type TEXT, stability_subtype TEXT CHECK(stability_subtype IN ('stable_monopoly','resource_monopoly_cyclical','high_dividend_watch')), inclusion_source TEXT CHECK(inclusion_source IN ('automatic_rule','manual_addition','manual_review')), inclusion_reason TEXT, risk_note TEXT, is_enabled INTEGER DEFAULT 1, included_at TEXT, updated_at TEXT, PRIMARY KEY(market,symbol));
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
    assert page.headers['cache-control'] == 'no-store'
    assert 'src="/dividend-universe.js?v=high-watch-page-1"' in page.text
    assert 'href="/styles.css?v=d3-universe-yields-3"' in page.text
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


def test_universe_frontend_left_joins_yield_snapshots_and_keeps_d1_actions_separate():
    script = (Path(__file__).parents[1] / 'frontend' / 'dividend-universe.js').read_text(encoding='utf-8')
    page = (Path(__file__).parents[1] / 'frontend' / 'dividend-universe.html').read_text(encoding='utf-8')
    assert "'/api/dividend/universe?' + params" in script
    assert "api('/api/dividend/yields')" in script
    assert "`${item.market || 'CN'}:${item.symbol}`" in script
    assert 'yieldByKey[yieldKey(item)]' in script
    assert 'snapshot?.latest_price' in script
    assert 'snapshot?.latest_year_yield' in script
    assert 'snapshot?.three_year_average_yield' in script
    assert "api('/api/dividend/yields/refresh'" in script
    assert "api('/api/dividend/universe/rescan'" in script
    assert 'id="refresh-yields"' in page
    assert 'id="dialog-close"' in page
    assert "$('dialog-close').onclick" in script
    assert "$('dialog').addEventListener('click'" in script
    assert 'data-sort-key="${key}"' in script
    assert "sortDirection === 'desc' ? 'asc' : 'desc'" in script
    assert "if (a == null) return b == null ? 0 : 1" in script
    assert "id=\"dividend-top-scroll\"" in page
    assert "$('dividend-top-scroll').addEventListener('scroll'" in script
    assert "window.addEventListener('resize', syncScrollbars)" in script


def _scan_files(tmp_path):
    csv_path = tmp_path / "scan.csv"
    fields = [
        "symbol", "company_name", "industry", "industry_level_1", "industry_level_2",
        "suggested_stability_subtype", "2023_event_count", "2024_event_count", "2025_event_count",
        "2023_dps", "2023_reference_date", "2023_reference_price", "2023_historical_yield",
        "2024_dps", "2024_reference_date", "2024_reference_price", "2024_historical_yield",
        "2025_dps", "2025_reference_date", "2025_reference_price", "2025_historical_yield",
        "three_year_historical_average_yield", "three_year_average_dps", "latest_price",
        "price_date", "latest_year_yield", "three_year_average_yield", "already_in_universe",
    ]
    row = {
        "symbol": "600002.SH", "company_name": "Watch Co", "industry": "Consumer",
        "industry_level_1": "Consumer", "industry_level_2": "Food",
        "suggested_stability_subtype": "high_dividend_watch",
        "2023_event_count": 1, "2024_event_count": 2, "2025_event_count": 1,
        "2023_dps": 1, "2024_dps": 1.1, "2025_dps": 1.2,
        "2023_reference_date": "2023-12-29", "2024_reference_date": "2024-12-31", "2025_reference_date": "2025-12-31",
        "2023_reference_price": 10, "2024_reference_price": 10, "2025_reference_price": 10,
        "2023_historical_yield": .1, "2024_historical_yield": .11, "2025_historical_yield": .12,
        "three_year_historical_average_yield": .11, "three_year_average_dps": 1.1,
        "latest_price": 11, "price_date": "2026-08-07", "latest_year_yield": .109,
        "three_year_average_yield": .1, "already_in_universe": False,
    }
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerow(row)
    summary_path = csv_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps({"summary": {
        "qualified_count": 1, "high_dividend_watch_count": 1,
        "stable_monopoly_count": 0, "resource_monopoly_cyclical_count": 0,
        "already_in_universe_count": 0, "new_candidate_count": 1,
        "elapsed_seconds": 111, "completed_at": "2026-08-08T00:00:00+00:00",
    }}), encoding="utf-8")
    return csv_path, summary_path


def test_latest_scan_is_file_backed_and_high_watch_add_requires_confirmation(monkeypatch, tmp_path):
    connection, client = _client(monkeypatch)
    connection.execute("INSERT INTO a_share_security_master VALUES ('600002.SH','Watch Co','2010-01-01',1,NULL,'SH')")
    connection.execute("INSERT INTO a_share_security_status_history VALUES ('600002.SH','2026-01-01',0)")
    connection.execute("INSERT INTO industry_memberships_current VALUES ('600002.SH','Consumer','Food','Food','test')")
    connection.commit()
    csv_path, summary_path = _scan_files(tmp_path)
    monkeypatch.setattr(universe_api, "SCAN_OUTPUT", csv_path)
    monkeypatch.setattr(universe_api, "SCAN_SUMMARY", summary_path)
    monkeypatch.setattr(universe_api, "connect_readonly", lambda: SharedConnection(connection))

    latest = client.get("/api/dividend/universe/rescan/latest")
    assert latest.status_code == 200
    assert latest.json()["items"][0]["suggested_stability_subtype"] == "high_dividend_watch"
    assert client.post("/api/dividend/universe/rescan/candidates/600002.SH/add", json={"confirm": False}).status_code == 422
    added = client.post("/api/dividend/universe/rescan/candidates/600002.SH/add", json={"confirm": True})
    assert added.status_code == 200 and added.json()["status"] == "added"
    stored = connection.execute("SELECT stability_subtype,inclusion_source FROM dividend_stable_universe WHERE symbol='600002.SH'").fetchone()
    assert tuple(stored) == ("high_dividend_watch", "manual_review")
    assert connection.execute("SELECT COUNT(*) FROM annual_cash_dividend_summaries WHERE symbol='600002.SH'").fetchone()[0] == 3
    assert client.post("/api/dividend/universe/rescan/candidates/600002.SH/add", json={"confirm": True}).json()["status"] == "already_exists"


def test_page_loads_latest_candidates_but_only_button_posts_rescan():
    script = (Path(__file__).parents[1] / "frontend" / "dividend-universe.js").read_text(encoding="utf-8")
    page = (Path(__file__).parents[1] / "frontend" / "dividend-universe.html").read_text(encoding="utf-8")
    assert "api('/api/dividend/universe/rescan/latest')" in script
    assert "$('rescan').onclick" in script
    assert "api('/api/dividend/universe/rescan', {method: 'POST'" in script
    assert "正在重新筛选…" in script
    assert "api('/api/dividend/yields/refresh'" in script
    assert "high_dividend_watch: '普通高股息观察型'" in script
    assert 'id="candidate-search"' in page and 'id="candidate-subtype"' in page and 'id="candidate-sort"' in page
