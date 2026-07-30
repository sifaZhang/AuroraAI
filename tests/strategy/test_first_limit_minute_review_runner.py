import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.expectation_gap.database import connect, migrate
from backend.strategy.first_limit.contracts import DataSource
from backend.strategy.first_limit.rules import normalize_symbol
from backend.strategy.first_limit.run_minute_review import (
    export_results,
    human_report,
    main,
    run_minute_review,
)
from backend.strategy.first_limit.sync_repository import MinuteBar as CacheMinuteBar


NOW = "2026-01-01T00:00:00+00:00"
TZ = ZoneInfo("Asia/Shanghai")


def database(tmp_path, name="minute-review.db"):
    connection = connect(tmp_path / name)
    migrate(connection)
    return connection


def seed_source(connection, run_id="daily-source", symbol="000001.SZ"):
    if connection.execute("SELECT 1 FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone() is None:
        connection.execute(
            """INSERT INTO backtest_runs(
             run_id,parameters_json,parameter_hash,status,backtest_version,backtest_scope,
             detection_version,start_date,end_date,data_cutoff_date,symbols_json,is_dry_run,
             started_at,created_at,updated_at)
           VALUES(?,?,?,'success','daily_backtest_v1','daily_proxy','first_limit_v1',
                  '2026-02-23','2026-02-23','2026-02-26',?,0,?,?,?)""",
            (run_id, "{}", "hash", json.dumps([symbol]), NOW, NOW, NOW),
        )
    event_id = connection.execute(
        """INSERT INTO first_limit_events(
             symbol,exchange,trade_date,detection_version,detection_status,is_limit_up_close,
             touched_upper_limit,is_first_limit,is_one_word_limit,is_consecutive_limit,
             consecutive_limit_days,lookback_trading_days,observed_lookback_days,open,
             exclusion_reasons,quality_flags,detected_at,created_at,updated_at)
           VALUES(?,?,?,'first_limit_v1','detected',1,1,1,0,0,1,20,20,10,'[]','[]',?,?,?)""",
        (symbol, "SZ", "2026-02-20", NOW, NOW, NOW),
    ).lastrowid
    observation_id = connection.execute(
        """INSERT INTO first_limit_pullback_observations(
             event_id,symbol,first_limit_date,observation_date,trading_day_offset,detection_version,
             scoring_version,pullback_version,observation_status,classification,pool_status,is_eliminated,
             earned_score,theoretical_max_score,determinable_max_score,coverage_ratio,is_complete,
             is_approximate,created_at,updated_at)
           VALUES(?,?,?,?,3,'first_limit_v1','first_limit_quality_v1','first_limit_pullback_v1',
                  'pass','A1','candidate',0,25,30,30,1,1,0,?,?)""",
        (event_id, symbol, "2026-02-20", "2026-02-23", NOW, NOW),
    ).lastrowid
    connection.execute(
        """INSERT INTO first_limit_pullback_components(
             observation_id,component_key,component_status,earned_score,max_score,raw_value_json,
             reasons_json,source_table,source_date,is_approximate)
           VALUES(?,'key_support','scored',6,6,'{"mode":"P1"}','[]','a_share_daily_bars',
                  '2026-02-23',0)""",
        (observation_id,),
    )
    connection.execute(
        """INSERT INTO first_limit_context_scores(
             event_id,observation_id,symbol,first_limit_date,observation_date,detection_version,
             scoring_version,pullback_version,context_scoring_version,score_status,first_limit_score,
             pullback_score,industry_score,market_score,stock_trend_score,daily_base_score,
             daily_base_theoretical_max_score,daily_base_determinable_max_score,
             daily_base_coverage_ratio,is_complete,is_approximate,minute_confirm_status,
             final_candidate_level,created_at,updated_at)
           VALUES(?,?,?,?,?,'first_limit_v1','first_limit_quality_v1','first_limit_pullback_v1',
                  'first_limit_context_v1','complete',18,25,18,8,9,78,90,90,1,1,0,
                  'not_available','pending_minute_confirmation',?,?)""",
        (event_id, observation_id, symbol, "2026-02-20", "2026-02-23", NOW, NOW),
    )
    signal_id = connection.execute(
        """INSERT INTO backtest_signals(
             run_id,event_id,observation_id,symbol,first_limit_date,observation_date,trading_day_offset,
             detection_version,scoring_version,pullback_version,context_scoring_version,backtest_version,
             daily_base_score,signal_status,signal_available_at,approximate_entry,lookahead_check)
           VALUES(?,?,?,?,?,?,3,'first_limit_v1','first_limit_quality_v1','first_limit_pullback_v1',
                  'first_limit_context_v1','daily_backtest_v1',78,'accepted',?,1,'cutoff_enforced')""",
        (run_id, event_id, observation_id, symbol, "2026-02-20", "2026-02-23", "2026-02-23"),
    ).lastrowid
    trade_id = connection.execute(
        """INSERT INTO backtest_trades(
             signal_id,entry_status,actual_entry_date,entry_price_raw,entry_price,shares,entry_cost,
             exit_status,created_at,updated_at)
           VALUES(?,'filled','2026-02-23',10.1,10.12,9800,25,'pending',?,?)""",
        (signal_id, NOW, NOW),
    ).lastrowid
    connection.execute(
        """INSERT INTO a_share_security_status_history(
             symbol,effective_date,board_type,is_st,is_suspended,no_price_limit,listed_date,
             source,quality_flags,updated_at)
           VALUES(?,'2026-02-20','MAIN',0,0,0,'2000-01-01','GM','[]',?)""",
        (symbol, NOW),
    )
    return trade_id


def minute(connection, symbol, stamp, o, h, low, close, volume=100):
    connection.execute(
        """INSERT INTO first_limit_minute_bars(
             symbol,bar_time,timeframe,open,high,low,close,volume,amount,data_source,
             adjustment,quality_flags,updated_at)
           VALUES(?,?,'1m',?,?,?,?,?,1000,'GM','none','[]',?)""",
        (symbol, stamp, o, h, low, close, volume, NOW),
    )


def seed_complete_minutes(connection, symbol="000001.SZ"):
    minute(connection, symbol, "2026-02-23T14:40:00+08:00", 10, 10.1, 9.95, 10, 100)
    minute(connection, symbol, "2026-02-23T14:41:00+08:00", 10, 10.2, 9.99, 10.1, 100)
    moment = datetime.fromisoformat("2026-02-23T14:42:00+08:00")
    end = datetime.fromisoformat("2026-02-23T15:00:00+08:00")
    while moment <= end:
        minute(connection, symbol, moment.isoformat(), 10.1, 10.15, 10, 10.1, 100)
        moment += timedelta(minutes=1)
    minute(connection, symbol, "2026-02-24T09:30:00+08:00", 10.2, 10.5, 10.1, 10.4, 100)
    minute(connection, symbol, "2026-02-24T09:31:00+08:00", 10.3, 10.4, 10.2, 10.3, 100)


def test_runner_reviews_only_pr67_trade_and_persists_groups_metrics_and_reports(tmp_path):
    connection = database(tmp_path)
    trade_id = seed_source(connection)
    seed_complete_minutes(connection)
    result = run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24", run_id="minute-run"
    )
    assert result["status"] == "success" and result["planned_count"] == 1
    review = dict(connection.execute("SELECT * FROM minute_review_results").fetchone())
    assert review["source_trade_id"] == trade_id
    assert review["confirmation_status"] == "confirmed"
    assert (review["classification"], review["trading_day_offset"], review["board_bucket"], review["protection_type"]) == ("A1", 3, "10pct", "P1")
    assert connection.execute("SELECT COUNT(*) FROM minute_review_stop_results").fetchone()[0] == 5
    assert connection.execute("SELECT COUNT(*) FROM minute_review_metrics WHERE scope='stop_rule'").fetchone()[0] == 5
    assert connection.execute("SELECT COUNT(*) FROM minute_review_metrics").fetchone()[0] >= 13
    assert '"stop_rule":"S1"' in export_results(connection, "minute-run")
    assert "PR6.8 分钟复核报告" in human_report(connection, "minute-run")


def test_missing_minutes_are_indeterminate_not_fake_confirmed(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    result = run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24", run_id="missing"
    )
    assert result["status"] == "partial"
    review = connection.execute("SELECT * FROM minute_review_results").fetchone()
    assert review["confirmation_status"] == "indeterminate"
    assert review["confirmation_reason"] == "missing_tail_minutes"
    assert connection.execute("SELECT COUNT(*) FROM minute_review_stop_results").fetchone()[0] == 0


def test_suspended_entry_day_is_explicitly_indeterminate(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    connection.execute(
        """UPDATE a_share_security_status_history SET effective_date='2026-02-23',is_suspended=1
           WHERE symbol='000001.SZ'"""
    )
    run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24", run_id="suspended"
    )
    review = connection.execute("SELECT * FROM minute_review_results").fetchone()
    assert review["confirmation_status"] == "indeterminate"
    assert review["confirmation_reason"] == "suspended_entry_day"


def test_confirmed_entry_with_missing_minutes_before_close_is_indeterminate(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    minute(connection, "000001.SZ", "2026-02-23T14:40:00+08:00", 10, 10.1, 9.95, 10)
    minute(connection, "000001.SZ", "2026-02-23T14:41:00+08:00", 10, 10.2, 9.99, 10.1)
    minute(connection, "000001.SZ", "2026-02-24T09:30:00+08:00", 9.9, 10, 9.8, 9.9)
    run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="missing-after-confirmation",
    )
    result = connection.execute(
        """SELECT s.status,s.trigger_reason
           FROM minute_review_stop_results s
           JOIN minute_review_results r ON r.id=s.review_result_id
           WHERE r.run_id='missing-after-confirmation' AND s.stop_rule='S1'"""
    ).fetchone()
    assert tuple(result) == ("indeterminate", "missing_minutes_after_entry_confirmation")


def test_scoped_fetcher_receives_only_source_trade_window_and_is_idempotent(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    calls = []

    def fetcher(symbol, start, end):
        calls.append((symbol, start.isoformat(), end.isoformat()))
        values = [
            ("2026-02-23T14:40:00+08:00", 10, 10.1, 9.95, 10),
            ("2026-02-23T14:41:00+08:00", 10, 10.2, 9.99, 10.1),
        ]
        moment = datetime.fromisoformat("2026-02-23T14:42:00+08:00")
        while moment <= datetime.fromisoformat("2026-02-23T15:00:00+08:00"):
            values.append((moment.isoformat(), 10.1, 10.15, 10, 10.1))
            moment += timedelta(minutes=1)
        values.extend([
            ("2026-02-24T09:30:00+08:00", 10.2, 10.5, 10.1, 10.4),
            ("2026-02-24T09:31:00+08:00", 10.3, 10.4, 10.2, 10.3),
        ])
        return [
            CacheMinuteBar(
                normalize_symbol(symbol), datetime.fromisoformat(stamp),
                o, h, low, close, 100, 1000, DataSource.GM,
            )
            for stamp, o, h, low, close in values
        ]

    run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="fetched", minute_fetcher=fetcher,
    )
    assert len(calls) == 1 and calls[0][0] == "000001.SZ"
    assert connection.execute("SELECT COUNT(*) FROM first_limit_minute_bars").fetchone()[0] == 23
    run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="fetched", resume=True, minute_fetcher=fetcher,
    )
    assert len(calls) == 1
    assert connection.execute("SELECT COUNT(*) FROM minute_review_results").fetchone()[0] == 1


def test_dry_run_resume_force_and_parameter_guard(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    seed_complete_minutes(connection)
    before = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("minute_review_runs", "minute_review_items", "minute_review_results")
    }
    dry = run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24", dry_run=True
    )
    assert dry["status"] == "dry_run"
    assert before == {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24", run_id="repeat"
    )
    original_id = connection.execute("SELECT id FROM minute_review_results").fetchone()[0]
    run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="repeat", resume=True,
    )
    assert connection.execute("SELECT id FROM minute_review_results").fetchone()[0] == original_id
    run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="repeat", resume=True, force=True,
    )
    assert connection.execute("SELECT COUNT(*) FROM minute_review_results").fetchone()[0] == 1
    with pytest.raises(ValueError, match="parameters"):
        run_minute_review(
            connection, source_run_id="daily-source", data_cutoff="2026-02-25",
            run_id="repeat", resume=True,
        )


def test_trade_failure_rolls_back_result_and_run_level_failure_converges(tmp_path):
    connection = database(tmp_path)
    trade_id = seed_source(connection)
    seed_complete_minutes(connection)

    def fail_trade(source_trade_id, stage):
        if stage == "before_save":
            raise RuntimeError("controlled trade failure")

    failed = run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="failed-item", failure_hook=fail_trade,
    )
    assert failed["status"] == "failed"
    assert connection.execute("SELECT COUNT(*) FROM minute_review_results WHERE run_id='failed-item'").fetchone()[0] == 0
    item = connection.execute("SELECT * FROM minute_review_items WHERE run_id='failed-item'").fetchone()
    assert item["source_trade_id"] == trade_id and item["status"] == "failed"

    def fail_run(stage):
        raise RuntimeError("controlled run failure")

    with pytest.raises(RuntimeError):
        run_minute_review(
            connection, source_run_id="daily-source", data_cutoff="2026-02-24",
            run_id="failed-run", run_failure_hook=fail_run,
        )
    failed_run = connection.execute(
        "SELECT status,planned_count FROM minute_review_runs WHERE run_id='failed-run'"
    ).fetchone()
    assert tuple(failed_run) == ("failed", 1)


def test_analysis_window_uses_at_most_three_open_sessions(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    connection.executemany(
        """INSERT INTO a_share_trading_calendar(
             market,trade_date,is_open,source,quality_flags,updated_at)
           VALUES('CN',?,1,'GM','[]',?)""",
        [(day, NOW) for day in ("2026-02-23", "2026-02-24", "2026-02-25", "2026-02-26")],
    )
    calls = []

    def fetcher(symbol, start, end):
        calls.append((symbol, start, end))
        return []

    result = run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-26",
        run_id="bounded-window", minute_fetcher=fetcher,
    )
    assert result["status"] == "partial"
    assert calls[0][1].isoformat() == "2026-02-23T14:40:00+08:00"
    assert calls[0][2].isoformat() == "2026-02-25T15:00:00+08:00"


def test_multiple_trades_isolate_failure_and_resume_only_failed_item(tmp_path):
    connection = database(tmp_path)
    first = seed_source(connection, symbol="000001.SZ")
    second = seed_source(connection, symbol="000002.SZ")
    seed_complete_minutes(connection, "000001.SZ")
    seed_complete_minutes(connection, "000002.SZ")

    def fail_second(trade_id, stage):
        if trade_id == second and stage == "before_save":
            raise RuntimeError("second trade failed")

    result = run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="isolated", failure_hook=fail_second,
    )
    assert result["status"] == "partial"
    assert connection.execute("SELECT COUNT(*) FROM minute_review_results WHERE run_id='isolated'").fetchone()[0] == 1
    items = {
        row["source_trade_id"]: row["status"]
        for row in connection.execute("SELECT * FROM minute_review_items WHERE run_id='isolated'")
    }
    assert items == {first: "success", second: "failed"}
    resumed = run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="isolated", resume=True,
    )
    assert resumed["status"] == "success"
    assert connection.execute("SELECT COUNT(*) FROM minute_review_results WHERE run_id='isolated'").fetchone()[0] == 2
    before_force = {
        row["symbol"]: row["id"]
        for row in connection.execute(
            "SELECT symbol,id FROM minute_review_results WHERE run_id='isolated'"
        )
    }
    run_minute_review(
        connection, source_run_id="daily-source", data_cutoff="2026-02-24",
        run_id="isolated", resume=True, force=True, force_symbols=["000001.SZ"],
    )
    after_force = {
        row["symbol"]: row["id"]
        for row in connection.execute(
            "SELECT symbol,id FROM minute_review_results WHERE run_id='isolated'"
        )
    }
    assert after_force["000001.SZ"] != before_force["000001.SZ"]
    assert after_force["000002.SZ"] == before_force["000002.SZ"]


def test_cli_and_database_integrity(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.db"
    connection = connect(path)
    migrate(connection)
    seed_source(connection)
    seed_complete_minutes(connection)
    connection.commit()
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{path.as_posix()}")
    assert main([
        "--source-run-id", "daily-source", "--data-cutoff", "2026-02-24",
        "--run-id", "cli-minute",
    ]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "success"
    assert len(payload["report"]["results"]) == 1
    assert payload["report"]["metrics"]
    checked = connect(path)
    assert checked.execute("PRAGMA foreign_key_check").fetchall() == []
    assert checked.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
