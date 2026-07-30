import json
import sqlite3

import pytest

from backend.expectation_gap.database import connect, migrate
from backend.strategy.first_limit.run_daily_backtest import (
    DEFAULT_VERSIONS,
    export_run,
    main,
    run_backtest,
)


NOW = "2026-01-01T00:00:00+00:00"


def seeded_connection(tmp_path):
    connection = connect(tmp_path / "runner.db")
    migrate(connection)
    return connection


def seed_candidate(connection, symbol="000001.SZ", event_date="2026-01-01", observation_date="2026-01-02"):
    event_id = connection.execute(
        """INSERT INTO first_limit_events(
             symbol,exchange,trade_date,detection_version,detection_status,is_limit_up_close,
             touched_upper_limit,is_first_limit,is_one_word_limit,is_consecutive_limit,
             consecutive_limit_days,lookback_trading_days,observed_lookback_days,open,
             exclusion_reasons,quality_flags,detected_at,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            symbol, "SZ", event_date, DEFAULT_VERSIONS["detection"], "detected", 1, 1, 1, 0, 0,
            1, 20, 20, 9, "[]", "[]", NOW, NOW, NOW,
        ),
    ).lastrowid
    observation_id = connection.execute(
        """INSERT INTO first_limit_pullback_observations(
             event_id,symbol,first_limit_date,observation_date,trading_day_offset,detection_version,
             scoring_version,pullback_version,observation_status,classification,pool_status,
             is_eliminated,earned_score,theoretical_max_score,determinable_max_score,coverage_ratio,
             is_complete,is_approximate,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, symbol, event_date, observation_date, 2, DEFAULT_VERSIONS["detection"],
            DEFAULT_VERSIONS["quality"], DEFAULT_VERSIONS["pullback"], "pass", "A1", "candidate",
            0, 25, 30, 30, 1, 1, 0, NOW, NOW,
        ),
    ).lastrowid
    connection.execute(
        """INSERT INTO first_limit_context_scores(
             event_id,observation_id,symbol,first_limit_date,observation_date,detection_version,
             scoring_version,pullback_version,context_scoring_version,score_status,first_limit_score,
             pullback_score,daily_base_score,daily_base_theoretical_max_score,
             daily_base_determinable_max_score,daily_base_coverage_ratio,is_complete,is_approximate,
             minute_confirm_status,final_candidate_level,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, observation_id, symbol, event_date, observation_date,
            DEFAULT_VERSIONS["detection"], DEFAULT_VERSIONS["quality"], DEFAULT_VERSIONS["pullback"],
            DEFAULT_VERSIONS["context"], "complete", 45, 25, 70, 90, 90, 1, 1, 0,
            "not_available", "pending_minute_confirmation", NOW, NOW,
        ),
    )
    return event_id


def seed_bar(connection, symbol, day, o=10, h=11, low=9, close=10, volume=100, amount=100, lower=None):
    connection.execute(
        """INSERT INTO a_share_daily_bars(
             stock_code,trade_date,open,high,low,close,volume,amount,source,adjustment,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,'none',?)""",
        (symbol.split(".")[0], day, o, h, low, close, volume, amount, "test", NOW),
    )
    if lower is not None:
        connection.execute(
            """INSERT INTO first_limit_daily_metadata(
                 symbol,trade_date,source_lower_limit,data_source,quality_flags,updated_at)
               VALUES(?,?,?,'CALCULATED','[]',?)""",
            (symbol, day, lower, NOW),
        )


def kwargs(symbols=("000001.SZ",), cutoff="2026-01-03"):
    return {
        "start_date": "2026-01-02",
        "end_date": "2026-01-02",
        "data_cutoff": cutoff,
        "symbols": symbols,
        "versions": DEFAULT_VERSIONS,
    }


def test_runner_closed_round_trip_and_resume_are_idempotent(tmp_path):
    connection = seeded_connection(tmp_path)
    seed_candidate(connection)
    seed_bar(connection, "000001.SZ", "2026-01-02")
    seed_bar(connection, "000001.SZ", "2026-01-03", o=8, h=9, low=8, close=8)
    result = run_backtest(connection, run_id="closed-run", **kwargs())
    assert result["status"] == "success"
    trade = dict(connection.execute("SELECT * FROM backtest_trades").fetchone())
    assert trade["terminal_status"] == "closed"
    assert trade["exit_signal_date"] == "2026-01-03"
    assert trade["actual_exit_date"] == "2026-01-03"
    assert trade["gross_return"] is not None and trade["net_return"] is not None
    item = dict(connection.execute("SELECT * FROM backtest_run_items").fetchone())
    assert (item["status"], item["trade_count"], item["closed_count"], item["unresolved_count"]) == ("success", 1, 1, 0)
    run_backtest(connection, run_id="closed-run", resume=True, **kwargs())
    assert connection.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("delay_count", "reason"),
    [(5, "five_untradable_exit_days"), (2, "data_ended")],
)
def test_runner_persists_both_open_unresolved_reasons(tmp_path, delay_count, reason):
    connection = seeded_connection(tmp_path)
    seed_candidate(connection)
    seed_bar(connection, "000001.SZ", "2026-01-02")
    seed_bar(connection, "000001.SZ", "2026-01-03", 8, 8, 8, 8, lower=8)
    for number in range(1, delay_count + 1):
        seed_bar(connection, "000001.SZ", f"2026-01-{3 + number:02d}", 8, 8, 8, 8, lower=8)
    cutoff = f"2026-01-{3 + delay_count:02d}"
    result = run_backtest(connection, run_id=f"unresolved-{delay_count}", **kwargs(cutoff=cutoff))
    trade = dict(connection.execute("SELECT * FROM backtest_trades").fetchone())
    assert result["status"] == "success"
    assert trade["terminal_status"] == "open_unresolved"
    assert trade["unresolved_reason"] == reason
    assert trade["exit_delay_market_days"] == delay_count
    assert all(trade[key] is None for key in ("actual_exit_date", "exit_price", "gross_return", "net_return"))
    run = dict(connection.execute("SELECT * FROM backtest_runs").fetchone())
    assert run["unresolved_count"] == 1
    assert result["portfolio"]["complete_return_count"] == 0


def test_runner_enforces_data_cutoff_and_ignores_future_bars(tmp_path):
    connection = seeded_connection(tmp_path)
    seed_candidate(connection)
    seed_bar(connection, "000001.SZ", "2026-01-02")
    seed_bar(connection, "000001.SZ", "2026-01-03", 8, 8, 8, 8, lower=8)
    seed_bar(connection, "000001.SZ", "2026-01-04", 8, 8, 8, 8, lower=8)
    seed_bar(connection, "000001.SZ", "2026-01-05", 20, 21, 19, 20)
    run_backtest(connection, run_id="cutoff", **kwargs(cutoff="2026-01-04"))
    trade = dict(connection.execute("SELECT * FROM backtest_trades").fetchone())
    assert trade["terminal_status"] == "open_unresolved"
    assert trade["unresolved_reason"] == "data_ended"
    assert trade["exit_delay_market_days"] == 1
    assert connection.execute("SELECT COUNT(*) FROM backtest_exit_delays").fetchone()[0] == 1


@pytest.mark.parametrize("delay_count", range(1, 6))
def test_runner_closes_on_each_exit_delay_day_at_that_days_open(tmp_path, delay_count):
    connection = seeded_connection(tmp_path)
    seed_candidate(connection)
    seed_bar(connection, "000001.SZ", "2026-01-02")
    seed_bar(connection, "000001.SZ", "2026-01-03", 8, 8, 8, 8, lower=8)
    for number in range(1, delay_count):
        seed_bar(connection, "000001.SZ", f"2026-01-{3 + number:02d}", 8, 8, 8, 8, lower=8)
    recovery_date = f"2026-01-{3 + delay_count:02d}"
    seed_bar(connection, "000001.SZ", recovery_date, 10, 11, 9, 10)
    run_backtest(
        connection,
        run_id=f"delay-{delay_count}",
        **kwargs(cutoff=recovery_date),
    )
    trade = dict(connection.execute("SELECT * FROM backtest_trades").fetchone())
    delays = list(connection.execute("SELECT * FROM backtest_exit_delays ORDER BY delay_market_day_number"))
    assert trade["terminal_status"] == "closed"
    assert trade["actual_exit_date"] == recovery_date
    assert trade["exit_delay_market_days"] == delay_count
    assert trade["exit_price"] == 9.99
    assert len(delays) == delay_count
    assert delays[-1]["market_date"] == recovery_date and delays[-1]["order_status"] == "filled"


def test_runner_time_exit_uses_tenth_valid_holding_day(tmp_path):
    connection = seeded_connection(tmp_path)
    seed_candidate(connection)
    seed_bar(connection, "000001.SZ", "2026-01-02")
    for number in range(1, 11):
        day = f"2026-01-{2 + number:02d}"
        seed_bar(connection, "000001.SZ", day, 10, 10.5, 9.5, 10)
    run_backtest(connection, run_id="time-exit", **kwargs(cutoff="2026-01-12"))
    trade = dict(connection.execute("SELECT * FROM backtest_trades").fetchone())
    assert trade["terminal_status"] == "closed"
    assert trade["exit_reason"] == "max_holding_days"
    assert trade["holding_days"] == 10
    assert trade["actual_exit_date"] == "2026-01-12"


def test_symbol_failure_rolls_back_business_writes_and_isolates_other_symbols(tmp_path):
    connection = seeded_connection(tmp_path)
    for symbol in ("000001.SZ", "000002.SZ"):
        seed_candidate(connection, symbol)
        seed_bar(connection, symbol, "2026-01-02")
        seed_bar(connection, symbol, "2026-01-03", o=8, h=9, low=8, close=8)

    def fail_second(symbol, stage):
        if symbol == "000002.SZ" and stage == "before_resolve":
            raise RuntimeError("controlled symbol failure")

    result = run_backtest(
        connection,
        run_id="isolated",
        failure_hook=fail_second,
        **kwargs(("000001.SZ", "000002.SZ")),
    )
    assert result["status"] == "partial"
    assert connection.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0] == 1
    items = {row["symbol"]: dict(row) for row in connection.execute("SELECT * FROM backtest_run_items")}
    assert items["000001.SZ"]["status"] == "success"
    assert items["000002.SZ"]["status"] == "failed"
    assert items["000002.SZ"]["error_type"] == "RuntimeError"
    assert "controlled symbol failure" in items["000002.SZ"]["last_error"]


def test_run_level_error_converges_running_ledger_to_failed(tmp_path):
    connection = seeded_connection(tmp_path)
    seed_candidate(connection)

    def fail_run(stage):
        raise RuntimeError(f"controlled run failure at {stage}")

    with pytest.raises(RuntimeError):
        run_backtest(connection, run_id="run-failure", run_failure_hook=fail_run, **kwargs())
    run = dict(connection.execute("SELECT * FROM backtest_runs WHERE run_id='run-failure'").fetchone())
    assert run["status"] == "failed"
    assert "controlled run failure" in run["last_error"]
    assert connection.execute("SELECT COUNT(*) FROM backtest_run_items").fetchone()[0] == 0


def test_resume_retries_failed_items_force_is_scoped_and_parameters_are_immutable(tmp_path):
    connection = seeded_connection(tmp_path)
    for symbol in ("000001.SZ", "000002.SZ"):
        seed_candidate(connection, symbol)
        seed_bar(connection, symbol, "2026-01-02")
        seed_bar(connection, symbol, "2026-01-03", o=8, h=9, low=8, close=8)

    def fail_second(symbol, stage):
        if symbol == "000002.SZ":
            raise RuntimeError("retry me")

    params = kwargs(("000001.SZ", "000002.SZ"))
    run_backtest(connection, run_id="resume", failure_hook=fail_second, **params)
    first_trade_id = connection.execute(
        """SELECT t.id FROM backtest_trades t JOIN backtest_signals s ON s.id=t.signal_id
           WHERE s.symbol='000001.SZ'"""
    ).fetchone()[0]
    run_backtest(connection, run_id="resume", resume=True, **params)
    assert connection.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0] == 2
    assert connection.execute("SELECT id FROM backtest_trades WHERE id=?", (first_trade_id,)).fetchone()
    run_backtest(connection, run_id="other-run", **params)
    other_trade_ids = {
        row[0]
        for row in connection.execute(
            """SELECT t.id FROM backtest_trades t JOIN backtest_signals s ON s.id=t.signal_id
               WHERE s.run_id='other-run'"""
        )
    }
    run_backtest(connection, run_id="resume", resume=True, force=True, **params)
    assert {
        row[0] for row in connection.execute("SELECT id FROM backtest_trades")
    }.issuperset(other_trade_ids)
    with pytest.raises(ValueError, match="parameters"):
        run_backtest(connection, run_id="resume", resume=True, **kwargs(("000001.SZ",), cutoff="2026-01-03"))


def test_dry_run_cli_export_metrics_and_ledger_integrity(tmp_path, monkeypatch, capsys):
    database = tmp_path / "cli.db"
    connection = connect(database)
    migrate(connection)
    seed_candidate(connection)
    seed_bar(connection, "000001.SZ", "2026-01-02")
    seed_bar(connection, "000001.SZ", "2026-01-03", o=8, h=9, low=8, close=8)
    connection.commit()
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{database.as_posix()}")
    before = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("backtest_runs", "backtest_run_items", "backtest_signals", "backtest_trades")
    }
    assert main([
        "--start-date", "2026-01-02", "--end-date", "2026-01-02",
        "--data-cutoff", "2026-01-03", "--symbols", "000001.SZ", "--dry-run",
    ]) == 0
    after = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert after == before
    assert main([
        "--start-date", "2026-01-02", "--end-date", "2026-01-02",
        "--data-cutoff", "2026-01-03", "--symbols", "000001.SZ", "--run-id", "cli-run",
    ]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "success"
    write_connection = connect(database)
    assert export_run(write_connection, "cli-run", "json") == export_run(write_connection, "cli-run", "json")
    csv_output = export_run(write_connection, "cli-run", "csv")
    assert csv_output.startswith("symbol,event_id,") and "000001.SZ" in csv_output
    summary = {
        row["metric_key"]: row["metric_value"]
        for row in write_connection.execute("SELECT * FROM backtest_metrics WHERE run_id='cli-run'")
    }
    assert summary["closed_count"] == 1 and summary["unresolved_count"] == 0
    with pytest.raises(sqlite3.IntegrityError):
        write_connection.execute(
            """INSERT INTO backtest_run_items(
                 run_id,item_key,symbol,status,started_at,updated_at)
               VALUES('cli-run','duplicate','000001.SZ','success',?,?)""",
            (NOW, NOW),
        )
    with pytest.raises(sqlite3.IntegrityError):
        write_connection.execute(
            """INSERT INTO backtest_run_items(
                 run_id,item_key,symbol,status,started_at,updated_at)
               VALUES('cli-run','bad-status','000002.SZ','unknown',?,?)""",
            (NOW, NOW),
        )
    assert write_connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert write_connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_cli_distinguishes_parameter_and_unexpected_runtime_errors(monkeypatch):
    common = [
        "--start-date", "2026-01-02", "--end-date", "2026-01-02",
        "--data-cutoff", "2026-01-01", "--symbols", "000001.SZ",
    ]
    assert main(common) == 2

    def fail_connect():
        raise RuntimeError("controlled connection failure")

    monkeypatch.setattr(
        "backend.strategy.first_limit.run_daily_backtest.connect",
        fail_connect,
    )
    valid = [
        "--start-date", "2026-01-02", "--end-date", "2026-01-02",
        "--data-cutoff", "2026-01-03", "--symbols", "000001.SZ",
    ]
    assert main(valid) == 3
