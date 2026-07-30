import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from backend.expectation_gap.database import connect, migrate
from backend.strategy.first_limit.minute_review import MinuteBar
from backend.strategy.first_limit.run_daily_candidates import (
    export_results,
    human_report,
    main,
    run_daily_candidates,
)

NOW = "2026-01-01T00:00:00+00:00"
TZ = ZoneInfo("Asia/Shanghai")


def database(tmp_path, name="daily-candidates.db"):
    connection = connect(tmp_path / name)
    migrate(connection)
    return connection


def seed_bar(connection, symbol, day, o=10.4, h=10.6, low=10, close=10.5, volume=50):
    connection.execute(
        """INSERT INTO a_share_daily_bars(
             stock_code,trade_date,open,high,low,close,volume,amount,source,
             adjustment,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,'none',?)""",
        (symbol.split(".")[0], day, o, h, low, close, volume, 1000, "test", NOW),
    )


def seed_event(connection, symbol="000001.SZ", event_date="2026-07-20"):
    event_id = connection.execute(
        """INSERT INTO first_limit_events(
             symbol,exchange,trade_date,detection_version,detection_status,
             is_limit_up_close,touched_upper_limit,is_first_limit,is_one_word_limit,
             is_consecutive_limit,consecutive_limit_days,lookback_trading_days,
             observed_lookback_days,open,high,low,close,exclusion_reasons,
             quality_flags,detected_at,created_at,updated_at)
           VALUES(?,? ,?,'first_limit_v1','detected',1,1,1,0,0,1,20,20,
                  10,11,9.8,11,'[]','[]',?,?,?)""",
        (symbol, symbol.split(".")[1], event_date, NOW, NOW, NOW),
    ).lastrowid
    return event_id


def seed_context(connection, event_id, symbol="000001.SZ", day="2026-07-22",
                 classification="A1", score=78, industry=15):
    observation_id = connection.execute(
        """INSERT INTO first_limit_pullback_observations(
             event_id,symbol,first_limit_date,observation_date,trading_day_offset,
             detection_version,scoring_version,pullback_version,observation_status,
             classification,pool_status,is_eliminated,earned_score,
             theoretical_max_score,determinable_max_score,coverage_ratio,is_complete,
             is_approximate,created_at,updated_at)
           VALUES(?,?, '2026-07-20',?,2,'first_limit_v1','first_limit_quality_v1',
                  'first_limit_pullback_v1','pass',?,'candidate',0,25,30,30,1,1,0,?,?)""",
        (event_id, symbol, day, classification, NOW, NOW),
    ).lastrowid
    connection.execute(
        """INSERT INTO first_limit_context_scores(
             event_id,observation_id,symbol,first_limit_date,observation_date,
             detection_version,scoring_version,pullback_version,
             context_scoring_version,score_status,first_limit_score,pullback_score,
             industry_score,market_score,stock_trend_score,daily_base_score,
             daily_base_determinable_max_score,daily_base_coverage_ratio,is_complete,
             is_approximate,minute_confirm_status,final_candidate_level,
             reasons_json,created_at,updated_at)
           VALUES(?,?,?,'2026-07-20',?,'first_limit_v1','first_limit_quality_v1',
                  'first_limit_pullback_v1','first_limit_context_v1','complete',
                  18,25,?,8,7,?,90,1,1,0,'not_available',
                  'pending_minute_confirmation','[]',?,?)""",
        (event_id, observation_id, symbol, day, industry, score, NOW, NOW),
    )


def seed_source(connection, symbol="000001.SZ", context_day="2026-07-22"):
    event_id = seed_event(connection, symbol)
    for day in ("2026-07-20", "2026-07-21", "2026-07-22"):
        connection.execute(
            """INSERT OR IGNORE INTO a_share_trading_calendar(
                 market,trade_date,is_open,source,quality_flags,updated_at)
               VALUES('CN',?,1,'GM','[]',?)""",
            (day, NOW),
        )
    connection.execute(
        """INSERT INTO a_share_security_status_history(
             symbol,effective_date,board_type,is_st,is_suspended,no_price_limit,
             listed_date,source,quality_flags,updated_at)
           VALUES(?,'2026-07-20','MAIN',0,0,0,'2000-01-01','GM','[]',?)""",
        (symbol, NOW),
    )
    prior_days = ("2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17")
    for day in prior_days:
        seed_bar(connection, symbol, day, volume=80)
    seed_bar(connection, symbol, "2026-07-20", o=10, h=11, low=9.8, close=11, volume=100)
    seed_bar(connection, symbol, "2026-07-21", volume=55)
    seed_bar(connection, symbol, "2026-07-22", volume=50)
    seed_context(connection, event_id, symbol, context_day)
    return event_id


def kwargs(**updates):
    values = {
        "trade_date": "2026-07-22",
        "stage": "close_confirmed",
        "as_of": "2026-07-22T15:00:00+08:00",
        "data_cutoff": "2026-07-22T15:00:00+08:00",
    }
    values.update(updates)
    return values


def test_close_runner_persists_evidence_stable_reports_and_idempotent_replay(tmp_path):
    connection = database(tmp_path)
    event_id = seed_source(connection)
    seed_bar(connection, "000001.SZ", "2026-07-23", low=1, close=1, volume=1000)
    result = run_daily_candidates(connection, run_id="close-run", **kwargs())
    assert result["status"] == "success"
    snapshot = connection.execute(
        "SELECT * FROM daily_candidate_snapshots"
    ).fetchone()
    assert (snapshot["first_limit_event_id"], snapshot["candidate_grade"]) == (event_id, "S")
    assert snapshot["lifecycle_status"] == "confirmed"
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_candidate_evidence"
    ).fetchone()[0] >= 8
    payload = export_results(connection, "close-run")
    assert payload["candidates"][0]["symbol"] == "000001.SZ"
    assert "PR6.9 每日候选" in human_report(connection, "close-run")
    seed_event(connection, "000002.SZ", "2026-07-21")
    replay = run_daily_candidates(connection, **kwargs())
    assert replay["run_id"] == "close-run"
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_candidate_snapshots"
    ).fetchone()[0] == 1


def test_tail_preview_is_immutable_bounded_and_close_is_newly_qualified(tmp_path):
    connection = database(tmp_path)
    event_id = seed_source(connection, context_day="2026-07-21")
    seed_context(connection, event_id, day="2026-07-22")
    calls = []

    def provider(symbol, start, end):
        calls.append((symbol, start, end))
        assert end.isoformat() == "2026-07-22T14:55:00+08:00"
        return iter([
            MinuteBar(datetime(2026, 7, 22, 14, 40, tzinfo=TZ), *(Decimal(str(x)) for x in (10, 10.1, 9.95, 10, 100)), None),
            MinuteBar(datetime(2026, 7, 22, 14, 41, tzinfo=TZ), *(Decimal(str(x)) for x in (10, 10.2, 9.99, 10.1, 100)), None),
        ])

    preview = run_daily_candidates(
        connection, run_id="preview", minute_provider=provider,
        **kwargs(
            stage="tail_preview",
            as_of="2026-07-22T14:55:00+08:00",
            data_cutoff="2026-07-22T14:55:00+08:00",
        ),
    )
    assert preview["status"] == "success", [
        dict(row) for row in connection.execute(
            "SELECT status,error_type,last_error FROM daily_candidate_items WHERE run_id='preview'"
        )
    ]
    assert len(calls) == 1
    preview_row = dict(connection.execute(
        "SELECT * FROM daily_candidate_snapshots WHERE run_id='preview'"
    ).fetchone())
    assert preview_row["lifecycle_status"] == "pending_close_confirmation"
    close = run_daily_candidates(connection, run_id="close", **kwargs())
    assert close["status"] == "success"
    close_row = connection.execute(
        "SELECT * FROM daily_candidate_snapshots WHERE run_id='close'"
    ).fetchone()
    assert close_row["change_type"] == "newly_qualified"
    assert close_row["preview_candidate_id"] == preview_row["id"]
    assert connection.execute(
        "SELECT lifecycle_status FROM daily_candidate_snapshots WHERE id=?",
        (preview_row["id"],),
    ).fetchone()[0] == "pending_close_confirmation"


def test_dry_run_writes_nothing_and_matches_formal_decision(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    before = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "daily_candidate_runs", "daily_candidate_items",
            "daily_candidate_snapshots", "daily_candidate_evidence",
        )
    }
    dry = run_daily_candidates(connection, dry_run=True, **kwargs())
    assert dry["status"] == "dry_run"
    assert dry["results"][0]["candidate_grade"] == "S"
    assert before == {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }


def test_resume_force_symbols_and_parameter_hash_guard(tmp_path):
    connection = database(tmp_path)
    first = seed_source(connection, "000001.SZ")
    second = seed_source(connection, "000002.SZ")
    run_daily_candidates(connection, run_id="scoped", **kwargs())
    original = {
        row["symbol"]: row["id"]
        for row in connection.execute(
            "SELECT symbol,id FROM daily_candidate_snapshots WHERE run_id='scoped'"
        )
    }
    run_daily_candidates(
        connection, run_id="scoped", resume=True, force=True,
        force_symbols=["000001.SZ"], **kwargs(),
    )
    current = {
        row["symbol"]: row["id"]
        for row in connection.execute(
            "SELECT symbol,id FROM daily_candidate_snapshots WHERE run_id='scoped'"
        )
    }
    assert current["000001.SZ"] != original["000001.SZ"]
    assert current["000002.SZ"] == original["000002.SZ"]
    assert set(current) == {"000001.SZ", "000002.SZ"}
    with pytest.raises(ValueError, match="parameters"):
        run_daily_candidates(
            connection, run_id="scoped", resume=True,
            **kwargs(data_cutoff="2026-07-22T15:01:00+08:00"),
        )


def test_event_transaction_rollback_failure_isolation_and_resume(tmp_path):
    connection = database(tmp_path)
    first = seed_source(connection, "000001.SZ")
    second = seed_source(connection, "000002.SZ")

    def fail_second(event_id, stage):
        if event_id == second and stage == "before_item":
            raise RuntimeError("controlled candidate failure")

    result = run_daily_candidates(
        connection, run_id="isolated", failure_hook=fail_second, **kwargs()
    )
    assert result["status"] == "partial"
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_candidate_snapshots WHERE run_id='isolated'"
    ).fetchone()[0] == 1
    items = {
        row["first_limit_event_id"]: row["status"]
        for row in connection.execute(
            "SELECT * FROM daily_candidate_items WHERE run_id='isolated'"
        )
    }
    assert items == {first: "success", second: "failed"}
    resumed = run_daily_candidates(
        connection, run_id="isolated", resume=True, **kwargs()
    )
    assert resumed["status"] == "success"
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_candidate_snapshots WHERE run_id='isolated'"
    ).fetchone()[0] == 2


def test_run_level_failure_converges_and_preserves_planned_count(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)

    def fail_run(stage):
        raise RuntimeError("controlled run failure")

    with pytest.raises(RuntimeError):
        run_daily_candidates(
            connection, run_id="run-failed", run_failure_hook=fail_run, **kwargs()
        )
    row = connection.execute(
        "SELECT status,planned_count FROM daily_candidate_runs WHERE run_id='run-failed'"
    ).fetchone()
    assert tuple(row) == ("failed", 1)


def test_same_symbol_multiple_events_use_event_natural_key(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    second = seed_event(connection, "000001.SZ", "2026-07-21")
    seed_context(connection, second, "000001.SZ", "2026-07-22", "A2")
    result = run_daily_candidates(connection, run_id="two-events", **kwargs())
    assert result["planned_count"] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_candidate_snapshots WHERE run_id='two-events'"
    ).fetchone()[0] == 2


def test_missing_calendar_and_status_are_indeterminate(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    connection.execute("DELETE FROM a_share_trading_calendar WHERE trade_date='2026-07-22'")
    result = run_daily_candidates(connection, run_id="missing-calendar", **kwargs())
    assert result["status"] == "partial"
    snapshot = connection.execute(
        "SELECT * FROM daily_candidate_snapshots WHERE run_id='missing-calendar'"
    ).fetchone()
    assert snapshot["lifecycle_status"] == "indeterminate"
    assert "MISSING_TRADING_CALENDAR" in snapshot["primary_reasons_json"]


def test_suspended_open_day_does_not_consume_observation_day(tmp_path):
    connection = database(tmp_path)
    seed_source(connection)
    connection.execute(
        """INSERT INTO a_share_security_status_history(
             symbol,effective_date,board_type,is_st,is_suspended,no_price_limit,
             listed_date,source,quality_flags,updated_at)
           VALUES('000001.SZ','2026-07-21','MAIN',0,1,0,'2000-01-01','GM','[]',?)""",
        (NOW,),
    )
    connection.execute(
        """INSERT INTO a_share_security_status_history(
             symbol,effective_date,board_type,is_st,is_suspended,no_price_limit,
             listed_date,source,quality_flags,updated_at)
           VALUES('000001.SZ','2026-07-22','MAIN',0,0,0,'2000-01-01','GM','[]',?)""",
        (NOW,),
    )
    run_daily_candidates(connection, run_id="suspension-offset", **kwargs())
    snapshot = connection.execute(
        """SELECT observation_day FROM daily_candidate_snapshots
           WHERE run_id='suspension-offset'"""
    ).fetchone()
    assert snapshot[0] == 1


def test_cli_migration_and_database_integrity(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.db"
    connection = connect(path)
    migrate(connection)
    seed_source(connection)
    connection.commit()
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{path.as_posix()}")
    assert main([
        "--trade-date", "2026-07-22", "--stage", "close_confirmed",
        "--data-cutoff", "2026-07-22T15:00:00+08:00",
        "--run-id", "cli-candidate",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["report"]["candidates"][0]["candidate_grade"] == "S"
    checked = connect(path)
    assert checked.execute("PRAGMA foreign_key_check").fetchall() == []
    assert checked.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
