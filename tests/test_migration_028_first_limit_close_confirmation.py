import sqlite3
from pathlib import Path
from backend.expectation_gap.database import connect,migrate

SNAPSHOT={"intraday_total_score","intraday_candidate_grade","official_industry_score","official_industry_rank",
"final_total_score","final_candidate_grade","final_buy_recommendation","confirmation_status",
"confirmation_change_type","confirmed_at"}
BACKTEST={"intraday_grade","final_grade","intraday_to_final_change","next_day_result"}


def test_fresh_028_and_project_idempotency(tmp_path):
    c=connect(tmp_path/"028.db");migrate(c);migrate(c)
    assert SNAPSHOT<={r[1] for r in c.execute("PRAGMA table_info(daily_candidate_snapshots)")}
    assert BACKTEST<={r[1] for r in c.execute("PRAGMA table_info(backtest_signals)")}


def test_old_tables_and_sentinels_survive_raw_028():
    c=sqlite3.connect(":memory:");c.execute("CREATE TABLE daily_candidate_snapshots(id INTEGER,symbol TEXT)")
    c.execute("CREATE TABLE backtest_signals(id INTEGER,symbol TEXT)")
    c.execute("INSERT INTO daily_candidate_snapshots VALUES(1,'candidate')");c.execute("INSERT INTO backtest_signals VALUES(2,'signal')")
    sentinels={"first_limit_events":("id INTEGER,symbol TEXT",(1,"event")),
        "first_limit_minute_bars":("symbol TEXT,bar_time TEXT",("bar","14:30")),
        "first_limit_sync_runs":("run_id TEXT,status TEXT",("run","success")),
        "backtest_parameter_results":("run_id TEXT,metric_json TEXT",("bt","{}"))}
    for table,(schema,values) in sentinels.items():c.execute(f"CREATE TABLE {table}({schema})");c.execute(f"INSERT INTO {table} VALUES(?,?)",values)
    before={table:tuple(c.execute(f"SELECT * FROM {table}").fetchone()) for table in sentinels}
    sql=Path("database/migrations/028_first_limit_close_confirmation.sql").read_text(encoding="utf-8");c.executescript(sql)
    assert tuple(c.execute("SELECT id,symbol FROM daily_candidate_snapshots").fetchone())==(1,"candidate")
    assert tuple(c.execute("SELECT id,symbol FROM backtest_signals").fetchone())==(2,"signal")
    assert all(tuple(c.execute(f"SELECT * FROM {table}").fetchone())==row for table,row in before.items())
