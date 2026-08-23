from __future__ import annotations

import json
import sqlite3

from backend.research.json_store import write_json_atomic
from backend.research.profile_builder import ProfileBuilder
from backend.research.profile_repository import ProfileRepository
from backend.research.research_type_mapping import load_mapping, resolve_research_type


def _connection():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE dividend_stable_universe(market TEXT,symbol TEXT,company_name TEXT,is_enabled INTEGER,grade TEXT,entry_yield REAL,add_yield REAL,heavy_yield REAL);
    CREATE TABLE dividend_yield_snapshots(market TEXT,symbol TEXT,calculation_date TEXT,latest_price REAL,three_year_average_dps REAL,three_year_average_yield REAL,price_date TEXT);
    CREATE TABLE industry_memberships_current(classification TEXT,classification_version TEXT,symbol TEXT,level1_code TEXT,level1_name TEXT,level2_code TEXT,level2_name TEXT,level3_code TEXT,level3_name TEXT);
    """)
    for symbol, name, enabled, grade, yield_value, l3 in (
        ("000858.SZ", "五粮液", 1, "A", .071, "白酒Ⅲ"), ("002372.SZ", "伟星新材", 0, "A", .071, "管材"),
        ("600461.SH", "洪城环境", 1, "S", .056, "水务及水治理"), ("600970.SH", "中材国际", 1, "B", .07, "国际工程"),
        ("000001.SZ", "Watch", 1, "S", .04, "未知"),
    ):
        con.execute("INSERT INTO dividend_stable_universe VALUES(?,?,?,?,?,?,?,?)", ("CN",symbol,name,enabled,grade,5,6,7))
        con.execute("INSERT INTO dividend_yield_snapshots VALUES(?,?,?,?,?,?,?)", ("CN",symbol,"2026-08-21",10,1,yield_value,"2026-08-21"))
        con.execute("INSERT INTO industry_memberships_current VALUES(?,?,?,?,?,?,?,?,?)", ("SW","2021",symbol,"l1","一级","l2","二级","l3",l3))
    return con


def test_repository_uses_yield_signal_not_grade_and_excludes_watch_or_disabled():
    repo = ProfileRepository(_connection())
    assert repo.profile_facts("000858.SZ")["yield_signal"] == "heavy"
    assert {x["symbol"] for x in repo.research_candidates()} == {"000858.SZ", "600461.SH", "600970.SH"}


def test_mapping_override_sw3_and_unresolved(tmp_path):
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("by_sw_l3:\n  白酒Ⅲ:\n    primary: consumer_brand\noverrides:\n  000858.SZ:\n    primary: override\n    secondary: cash_cow\n", encoding="utf-8")
    loaded = load_mapping(mapping)
    assert resolve_research_type("000858.SZ", "白酒Ⅲ", loaded)["source"] == "symbol_override"
    assert resolve_research_type("x", "白酒Ⅲ", loaded)["primary"] == "consumer_brand"
    assert resolve_research_type("x", "未知", loaded)["source"] == "unresolved"


def test_builder_is_idempotent_preserves_unknown_fields_and_does_not_reset_current(tmp_path):
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("by_sw_l3:\n  白酒Ⅲ:\n    primary: consumer_brand\noverrides:\n  000858.SZ:\n    primary: consumer_brand\n    secondary: high_dividend_cash_cow\n", encoding="utf-8")
    root = tmp_path / "research"
    builder = ProfileBuilder(ProfileRepository(_connection()), research_root=root, mapping_path=mapping)
    builder.build("000858.SZ")
    company = root / "000858.SZ" / "company.json"; current = root / "000858.SZ" / "current.json"
    payload = json.loads(company.read_text(encoding="utf-8")); assert payload["name"] == "五粮液" and payload["industry"]["sw_l3_name"] == "白酒Ⅲ"
    payload["notes"] = "manual"; write_json_atomic(company, payload)
    write_json_atomic(current, {"schema_version": 2, "status": "analyzed", "agent": "future"})
    builder.build("000858.SZ")
    assert json.loads(company.read_text(encoding="utf-8"))["notes"] == "manual"
    assert json.loads(current.read_text(encoding="utf-8"))["status"] == "analyzed"
    assert (root / "000858.SZ" / "sources" / "financial_reports").is_dir()


def test_atomic_json_store_is_utf8_and_terminated(tmp_path):
    path = tmp_path / "company.json"
    write_json_atomic(path, {"name": "五粮液"})
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "五粮液"
    assert path.read_bytes().endswith(b"\n") and not list(tmp_path.glob("*.tmp"))
