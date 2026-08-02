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
from backend.strategy.first_limit import daily_candidate_repository as candidate_repo
from backend.strategy.first_limit.daily_candidates import Decision
from backend.strategy.first_limit.close_confirmation import CloseConfirmationService

NOW = "2026-01-01T00:00:00+00:00"
TZ = ZoneInfo("Asia/Shanghai")


def database(tmp_path, name="daily-candidates.db"):
    connection = connect(tmp_path / name)
    migrate(connection)
    return connection


def seed_industry_context(connection, symbol="000001.SZ"):
    for level, code, parent in ((1, "L1", None), (2, "L2", "L1"), (3, "L3", "L2")):
        connection.execute(
            "INSERT INTO industry_nodes VALUES('SW','2021',?,?,?,?,?,'2026-07-22')",
            (code, f"行业{level}", level, parent, "test"),
        )
    connection.execute(
        "INSERT INTO industry_memberships_current VALUES('SW','2021',?,?,?,?,?,?,?,?,'2026-07-22')",
        (symbol, "L1", "行业1", "L2", "行业2", "L3", "行业3", "test"),
    )
    for day, score in (("2026-07-19", 41), ("2026-07-20", 63)):
        for level, code in ((1, "L1"), (2, "L2"), (3, "L3")):
            connection.execute(
                "INSERT INTO industry_daily_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (day,"SW","2021",code,level,8,8,8,0,0,1,1,1,1,0,0,1,0,1,0,0,0,None,None,1,1,"complete","{}",NOW),
            )
            connection.execute(
                "INSERT INTO industry_daily_scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (day,"SW","2021",code,level,score,1,1,1,1,1,1,1,None,None,None,"neutral",20,1,1,1,"high","industry_score_v1","{}",NOW),
            )


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


def test_candidate_path_persists_and_reloads_industry_context_evidence(tmp_path):
    connection = database(tmp_path, "industry-evidence.db")
    seed_source(connection)
    seed_industry_context(connection)
    result = run_daily_candidates(connection, run_id="industry-context", **kwargs())
    assert result["status"] == "success"
    snapshot = connection.execute(
        "SELECT * FROM daily_candidate_snapshots WHERE run_id='industry-context'"
    ).fetchone()
    assert tuple(snapshot[key] for key in (
        "sw_level1_code", "sw_level2_code", "sw_level3_code",
        "effective_industry_level", "effective_industry_code", "industry_context_status",
    )) == ("L1", "L2", "L3", 3, "L3", "complete")
    stored = connection.execute(
        "SELECT actual_value FROM daily_candidate_evidence WHERE candidate_id=? AND rule_code='INDUSTRY_CONTEXT'",
        (snapshot["id"],),
    ).fetchone()
    evidence = json.loads(stored["actual_value"])
    assert evidence["membership"] == {
        "classification": "SW", "classification_version": "2021", "symbol": "000001.SZ",
        "level1_code": "L1", "level1_name": "行业1", "level2_code": "L2",
        "level2_name": "行业2", "level3_code": "L3", "level3_name": "行业3",
        "source": "test", "updated_at": "2026-07-22",
    }
    assert evidence["effective"]["effective_level"] == 3
    assert evidence["effective"]["effective_industry_code"] == "L3"
    assert evidence["effective"]["effective_industry_name"] == "行业3"
    assert evidence["first_limit_score"] == 63 and evidence["first_limit_rank"] == 1
    assert evidence["previous_score"] == 63 and evidence["previous_rank"] == 1
    assert evidence["status"] == "complete"
    assert evidence["effective"]["effective_confidence"] == "high"
    assert evidence["effective"]["fallback_reason"] is None


def test_pr613b_missing_industry_minutes_are_not_a_hard_elimination(tmp_path):
    connection = database(tmp_path, "pr613b-eliminated.db")
    seed_source(connection)
    result = run_daily_candidates(
        connection, run_id="pr613b-eliminated",
        strategy_version="first_limit_candidate_score_v2",
        minute_provider=lambda *_: iter(()),
        **kwargs(stage="tail_preview", as_of="2026-07-22T14:55:00+08:00",
                 data_cutoff="2026-07-22T14:55:00+08:00"),
    )
    assert result["status"] == "success"
    summary = json.loads(connection.execute(
        "SELECT summary_json FROM daily_candidate_runs WHERE run_id='pr613b-eliminated'"
    ).fetchone()[0])
    assert summary["scanned_count"] == 1
    assert "INTRADAY_DATA_SEVERELY_INSUFFICIENT" not in summary[
        "elimination_reason_counts"
    ]


def test_pr613b_scoring_columns_and_all_evidence_round_trip(tmp_path):
    connection = database(tmp_path, "pr613b-evidence.db")
    event_id = seed_source(connection)
    event = connection.execute("SELECT * FROM first_limit_events WHERE id=?", (event_id,)).fetchone()
    params = {"trade_date":"2026-07-22","stage":"tail_preview","as_of":"2026-07-22T14:55:00+08:00",
        "data_cutoff":"2026-07-22T14:55:00+08:00","strategy_version":"first_limit_candidate_score_v2",
        "versions":{"detection":"first_limit_v1","pullback":"first_limit_pullback_v1","context":"first_limit_context_v1"}}
    candidate_repo.create_run(connection,"persist-score",params,"hash",1,True)
    candidate_repo.initialize_items(connection,"persist-score",[event])
    scoring={
        "INTRADAY_INDUSTRY_ESTIMATE":{"status":"complete","intraday_score":80,"intraday_rank":2,"trade_date":"2026-07-22","as_of_time":"14:55"},
        "CAPITAL_ACTIVITY":{"status":"complete","score":8},"LEADER_SCORE":{"status":"complete","score":9},
        "INDUSTRY_ENVIRONMENT":{"status":"complete","score":12,"trend":{"score":5}},
        "CANDIDATE_SCORE":{"version":"first_limit_candidate_score_v2","status":"complete","total_score":86,
            "grade":"S","buy_recommendation":"重点候选","hard_exclusions":[],"grade_caps":[]},
    }
    decision=Decision("eligible","S",Decimal("86"),2,(),())
    candidate_id=candidate_repo.save_candidate(connection,"persist-score",event,"2026-07-22","tail_preview",
        decision,params["versions"],params["strategy_version"],None,None,{"candidate_scoring":scoring})
    row=connection.execute("SELECT * FROM daily_candidate_snapshots WHERE id=?",(candidate_id,)).fetchone()
    assert (row["effective_score"],row["effective_rank"],row["capital_activity_score"],row["leader_score"],
        row["industry_trend_score"],row["industry_environment_score"],row["score"],row["candidate_grade"],
        row["buy_recommendation"],row["scoring_version"]) == (80,2,8,9,5,12,86,"S","重点候选","first_limit_candidate_score_v2")
    evidence={r["rule_code"]:json.loads(r["actual_value"]) for r in candidate_repo.evidence_for(connection,candidate_id)}
    assert set(scoring)<=set(evidence) and evidence["CANDIDATE_SCORE"]["total_score"]==86


def test_close_confirmation_pending_then_official_and_idempotent(tmp_path):
    connection=database(tmp_path,"close-confirmation.db");event_id=seed_source(connection);seed_industry_context(connection)
    event=connection.execute("SELECT * FROM first_limit_events WHERE id=?",(event_id,)).fetchone()
    params={"trade_date":"2026-07-22","stage":"tail_preview","as_of":"2026-07-22T14:55:00+08:00",
        "data_cutoff":"2026-07-22T14:55:00+08:00","strategy_version":"first_limit_candidate_score_v2",
        "versions":{"detection":"first_limit_v1","pullback":"first_limit_pullback_v1","context":"first_limit_context_v1"}}
    candidate_repo.create_run(connection,"confirm",params,"confirm-hash",1,True);candidate_repo.initialize_items(connection,"confirm",[event])
    components={"shape_pullback":35,"first_limit":20,"industry_environment":10,"capital_activity":8,"leader":8,"market_risk":7}
    scoring={"INTRADAY_INDUSTRY_ESTIMATE":{"status":"complete","intraday_score":75,"intraday_rank":1,
        "industry_level":3,"trade_date":"2026-07-22","as_of_time":"14:55"},
        "CANDIDATE_SCORE":{"version":"first_limit_candidate_score_v2","status":"complete","components":components,
        "total_score":88,"grade":"S","buy_recommendation":"重点候选","hard_exclusions":[],"grade_caps":[]}}
    snapshot_id=candidate_repo.save_candidate(connection,"confirm",event,"2026-07-22","tail_preview",
        Decision("eligible","S",Decimal("88"),2,(),()),params["versions"],params["strategy_version"],None,None,
        {"candidate_scoring":scoring})
    service=CloseConfirmationService(connection)
    assert service.confirm_snapshot(snapshot_id,dry_run=True)["status"]=="pending"
    for level,code in ((1,"L1"),(2,"L2"),(3,"L3")):
        connection.execute("INSERT INTO industry_daily_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-07-22","SW","2021",code,level,8,8,8,0,0,1,1,1,1,0,0,1,0,1,0,0,0,None,None,1,1,"complete","{}",NOW))
        connection.execute("INSERT INTO industry_daily_scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-07-22","SW","2021",code,level,70,1,1,1,1,1,1,1,None,None,None,"neutral",20,1,1,1,"high","industry_score_v1","{}",NOW))
    with connection: first=service.confirm_snapshot(snapshot_id)
    with connection: second=service.confirm_snapshot(snapshot_id)
    assert first["status"] in {"confirmed","removed"} and second["change"].change_type==first["change"].change_type
    row=connection.execute("SELECT * FROM daily_candidate_snapshots WHERE id=?",(snapshot_id,)).fetchone()
    assert row["official_industry_score"]==70 and row["confirmation_status"]==first["status"]
    codes={r[0] for r in connection.execute("SELECT rule_code FROM daily_candidate_evidence WHERE candidate_id=?",(snapshot_id,))}
    assert {"OFFICIAL_CLOSE_INDUSTRY","INDUSTRY_ESTIMATION_ERROR","CLOSE_CONFIRMATION","CANDIDATE_CHANGE"}<=codes


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
