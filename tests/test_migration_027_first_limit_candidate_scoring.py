import sqlite3
from backend.expectation_gap.database import connect,migrate

SNAPSHOT={"effective_score","effective_rank","capital_activity_score","leader_score",
"industry_trend_score","industry_environment_score","buy_recommendation","scoring_version"}


def test_fresh_migration_has_minimal_scoring_columns_and_summary(tmp_path):
    c=connect(tmp_path/"027.db");migrate(c)
    assert SNAPSHOT <= {r[1] for r in c.execute("PRAGMA table_info(daily_candidate_snapshots)")}
    assert "summary_json" in {r[1] for r in c.execute("PRAGMA table_info(daily_candidate_runs)")}


def test_raw_027_preserves_old_candidate_and_sentinels():
    c=sqlite3.connect(":memory:");c.execute("CREATE TABLE daily_candidate_snapshots(id INTEGER,symbol TEXT)")
    c.execute("CREATE TABLE daily_candidate_runs(run_id TEXT)");c.execute("INSERT INTO daily_candidate_snapshots VALUES(1,'x')")
    sentinels={"first_limit_events":("id INTEGER,symbol TEXT",(1,"event")),
        "first_limit_minute_bars":("symbol TEXT,bar_time TEXT",("bar","14:30")),
        "first_limit_sync_runs":("run_id TEXT,status TEXT",("run","success")),
        "backtest_parameter_results":("run_id TEXT,metric_json TEXT",("bt","{}"))}
    for table,(schema,values) in sentinels.items():
        c.execute(f"CREATE TABLE {table}({schema})");c.execute(f"INSERT INTO {table} VALUES(?,?)",values)
    before={table:tuple(c.execute(f"SELECT * FROM {table}").fetchone()) for table in sentinels}
    c.executescript(open("database/migrations/027_first_limit_candidate_scoring.sql",encoding="utf-8").read())
    assert tuple(c.execute("SELECT id,symbol FROM daily_candidate_snapshots").fetchone())==(1,"x")
    assert SNAPSHOT <= {r[1] for r in c.execute("PRAGMA table_info(daily_candidate_snapshots)")}
    assert all(tuple(c.execute(f"SELECT * FROM {table}").fetchone())==value for table,value in before.items())
