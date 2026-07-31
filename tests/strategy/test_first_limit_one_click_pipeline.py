import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.expectation_gap.database import connect, migrate
from backend.strategy.first_limit import pipeline_repository as repo
from backend.strategy.first_limit import pipeline_service as service
from backend.strategy.first_limit import run_one_click_pipeline as pipeline_cli

DAY = "2026-07-21"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def database(tmp_path, monkeypatch):
    path = tmp_path / "pipeline.db"
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{path.as_posix()}")
    connection = connect(path)
    migrate(connection)
    return path, connection


def seed_calendar(connection):
    start = date.fromisoformat(DAY) - timedelta(days=70)
    values = []
    current = start
    while current <= date.fromisoformat(DAY):
        values.append((
            "CN", str(current), int(current.weekday() < 5), "MANUAL", "[]",
            "2026-07-21T08:00:00+00:00",
        ))
        current += timedelta(days=1)
    connection.executemany(
        """INSERT INTO a_share_trading_calendar(
             market,trade_date,is_open,source,quality_flags,updated_at)
           VALUES(?,?,?,?,?,?)""",
        values,
    )
    connection.commit()


def create(connection, symbols=None):
    return service.create_job(
        connection, trade_date=DAY, stage="close_confirmed",
        as_of=f"{DAY}T15:00:00+08:00",
        data_cutoff=f"{DAY}T15:00:00+08:00", symbols=symbols,
    )


class FakeExecutor:
    def __init__(self, fail_once=None, partial=False):
        self.fail_once = fail_once
        self.partial = partial
        self.calls = []

    def run_step(self, code, context):
        self.calls.append(code)
        if code == self.fail_once:
            self.fail_once = None
            raise RuntimeError(r"Traceback C:\private\worker.py token=secret")
        if code == "coverage_validation":
            repo.save_coverage(
                context.connection, context.job_id, "fixture",
                expected_count=1, covered_count=0 if self.partial else 1,
                missing_count=1 if self.partial else 0,
                complete=not self.partial, details={"fixture": True},
            )
            return {
                "status": "partial" if self.partial else "success",
                "coverage_complete": not self.partial,
                "candidate_run_id": None,
            }
        return {"status": "success", "step": code}


def test_migration_job_identity_scope_and_integrity(tmp_path, monkeypatch):
    _path, connection = database(tmp_path, monkeypatch)
    seed_calendar(connection)
    first = create(connection)
    replay = create(connection)
    partial = create(connection, ["SZSE.000001", "000001.SZ"])
    assert first["reused"] is False
    assert replay == {**first, "reused": True}
    assert replay["job_id"] == first["job_id"]
    assert partial["job_id"] != first["job_id"]
    row = repo.job(connection, partial["job_id"])
    assert row["scope"] == "partial"
    assert repo.load(row["parameter_json"])["symbols"] == ["000001.SZ"]
    assert len(repo.steps(connection, first["job_id"])) == len(service.STEP_CODES)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_create_resumes_cancelled_natural_key_job(tmp_path, monkeypatch):
    _path, connection = database(tmp_path, monkeypatch)
    seed_calendar(connection)
    first = create(connection)
    job_id = first["job_id"]
    with connection:
        repo.claim(connection, job_id)
        repo.start_step(connection, job_id, service.STEP_CODES[0])
        repo.finish_step(
            connection, job_id, service.STEP_CODES[0], "success"
        )
        repo.cancel(connection, job_id)

    replay = create(connection)

    assert replay["job_id"] == job_id
    assert replay["status"] == "interrupted"
    assert replay["reused"] is True
    states = {
        row["step_code"]: row["status"]
        for row in repo.steps(connection, job_id)
    }
    assert states[service.STEP_CODES[0]] == "success"
    assert states[service.STEP_CODES[1]] == "pending"


def test_window_uses_open_day_dependencies_and_rejects_non_trading_day(
    tmp_path, monkeypatch
):
    _path, connection = database(tmp_path, monkeypatch)
    seed_calendar(connection)
    plan = service.plan_required_window(connection, DAY)
    assert len(plan["d0_dates"]) == 7
    assert plan["dependency_open_days"] == 29
    assert plan["required_end"] == DAY
    assert all(
        date.fromisoformat(value).weekday() < 5 for value in plan["open_dates"]
    )
    try:
        service.plan_required_window(connection, "2026-07-19")
    except service.PipelineError as exc:
        assert exc.code == "first_limit_non_trading_day"
    else:
        raise AssertionError("non-trading day should fail")


def test_success_partial_failure_resume_and_redaction(tmp_path, monkeypatch):
    _path, connection = database(tmp_path, monkeypatch)
    seed_calendar(connection)
    success_job = create(connection)["job_id"]
    success = service.execute_job(connection, success_job, FakeExecutor())
    assert success["status"] == "success"
    assert success["coverage_complete"] == 1
    assert all(
        row["status"] == "success"
        for row in repo.steps(connection, success_job)
    )

    partial_job = service.create_job(
        connection, trade_date=DAY, stage="tail_preview",
        as_of=f"{DAY}T14:55:00+08:00",
        data_cutoff=f"{DAY}T14:55:00+08:00",
    )["job_id"]
    partial = service.execute_job(
        connection, partial_job, FakeExecutor(partial=True)
    )
    assert partial["status"] == "partial"
    assert partial["coverage_complete"] == 0

    failure_job = create(connection, ["000002.SZ"])["job_id"]
    executor = FakeExecutor(fail_once="daily_bars")
    failed = service.execute_job(connection, failure_job, executor)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "first_limit_pipeline_step_failed"
    assert "secret" not in (failed["error_message"] or "").lower()
    succeeded_before_failure = [
        row["step_code"] for row in repo.steps(connection, failure_job)
        if row["status"] == "success"
    ]
    assert succeeded_before_failure
    with connection:
        _row, changed = repo.prepare_retry(connection, failure_job)
    assert changed is True
    resumed = service.execute_job(connection, failure_job, executor)
    assert resumed["status"] == "partial"  # controlled symbol scope
    assert all(code not in executor.calls[len(succeeded_before_failure):]
               for code in succeeded_before_failure)
    failures = repo.failures(connection, failure_job)[1]
    assert len(failures) == 1
    assert "token" not in failures[0]["error_message"].lower()


def test_recovery_marks_running_interrupted_and_preserves_success_steps(
    tmp_path, monkeypatch
):
    _path, connection = database(tmp_path, monkeypatch)
    seed_calendar(connection)
    job_id = create(connection)["job_id"]
    with connection:
        assert repo.claim(connection, job_id)
        repo.start_step(connection, job_id, "calendar")
        repo.finish_step(connection, job_id, "calendar", "success")
        repo.start_step(connection, job_id, "universe")
        assert repo.recover_stale(connection) == 1
    assert repo.job(connection, job_id)["status"] == "interrupted"
    states = {row["step_code"]: row["status"] for row in repo.steps(connection, job_id)}
    assert states["calendar"] == "success"
    assert states["universe"] == "interrupted"
    with connection:
        repo.prepare_retry(connection, job_id)
    assert {row["step_code"]: row["status"] for row in repo.steps(connection, job_id)}[
        "calendar"
    ] == "success"


def test_full_universe_comes_from_provider_not_current_master(
    tmp_path, monkeypatch
):
    _path, connection = database(tmp_path, monkeypatch)
    seed_calendar(connection)
    connection.execute(
        """INSERT INTO a_share_security_master(
             symbol,stock_code,exchange,board_type,security_name,is_active,
             source,quality_flags,updated_at)
           VALUES('000001.SZ','000001','SZ','MAIN','only local',1,
                  'MANUAL','[]',?)""",
        ("2026-07-21T00:00:00+00:00",),
    )
    job_id = create(connection)["job_id"]
    parameters = repo.load(repo.job(connection, job_id)["parameter_json"])

    class Provider:
        api = object()

        def list_universe(self, *_args):
            return [
                {"symbol": "SZSE.000001", "sec_type": 1, "sec_name": "A"},
                {"symbol": "SHSE.600000", "sec_type": 1, "sec_name": "B"},
                {"symbol": "SZSE.000003", "sec_type": 1, "sec_name": "ST C"},
            ]

    context = service.PipelineContext(
        connection, job_id, parameters, Provider()
    )
    output = service.DefaultExecutor(Provider()).run_step("universe", context)
    assert output["total_symbols"] == 3
    assert output["eligible_symbols"] == 2
    assert [row["symbol"] for row in repo.universe(connection, job_id)] == [
        "000001.SZ", "600000.SH",
    ]


def test_pipeline_api_is_async_reuses_and_exposes_audit(
    tmp_path, monkeypatch
):
    _path, connection = database(tmp_path, monkeypatch)
    seed_calendar(connection)
    connection.close()
    started = []
    monkeypatch.setattr(
        service, "start_background", lambda job_id: started.append(job_id)
    )
    client = TestClient(app)
    payload = {
        "trade_date": DAY, "stage": "close_confirmed",
        "as_of": f"{DAY}T15:00:00+08:00",
        "data_cutoff": f"{DAY}T15:00:00+08:00",
    }
    first = client.post("/api/first-limit/pipeline-jobs", json=payload)
    assert first.status_code == 202, first.text
    replay = client.post("/api/first-limit/pipeline-jobs", json=payload)
    assert replay.status_code == 202
    assert replay.json()["job_id"] == first.json()["job_id"]
    assert replay.json()["reused"] is True
    assert started == [first.json()["job_id"], first.json()["job_id"]]
    detail = client.get(first.json()["poll_url"])
    assert detail.status_code == 200
    assert "parameter_hash" not in detail.json()
    steps = client.get(
        f"{first.json()['poll_url']}/steps"
    ).json()
    assert len(steps["items"]) == len(service.STEP_CODES)
    assert client.get(
        f"{first.json()['poll_url']}/coverage"
    ).status_code == 200
    assert client.get(
        f"{first.json()['poll_url']}/failures"
    ).status_code == 200
    assert client.get("/api/first-limit/pipeline-jobs/999999").status_code == 404


def test_cli_wait_report_and_resume_parameter_conflict(
    tmp_path, monkeypatch, capsys
):
    _path, connection = database(tmp_path, monkeypatch)
    seed_calendar(connection)
    connection.close()

    original_execute = service.execute_job

    def execute(connection, job_id):
        executor = FakeExecutor()
        return original_execute(connection, job_id, executor)

    monkeypatch.setattr(pipeline_cli.service, "execute_job", execute)
    code = pipeline_cli.main([
        "--trade-date", DAY, "--stage", "close_confirmed",
        "--as-of", f"{DAY}T15:00:00+08:00",
        "--data-cutoff", f"{DAY}T15:00:00+08:00",
        "--wait", "--report", "json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["coverage_complete"] is True
    conflict = pipeline_cli.main([
        "--resume-job-id", str(payload["id"]), "--trade-date", DAY,
        "--stage", "close_confirmed",
    ])
    assert conflict == 2
    assert "cannot be combined" in capsys.readouterr().out


def test_future_date_and_future_cutoff_are_rejected():
    future_now = datetime(2026, 7, 21, 10, 0, tzinfo=SHANGHAI)
    try:
        service.normalize_parameters(
            trade_date="2026-07-22", stage="close_confirmed",
            data_cutoff="2026-07-22T15:00:00+08:00", now=future_now,
        )
    except service.PipelineError as exc:
        assert exc.code == "first_limit_pipeline_future_date"
    else:
        raise AssertionError("future date should fail")
    try:
        service.normalize_parameters(
            trade_date="2026-07-21", stage="tail_preview",
            as_of="2026-07-21T14:55:00+08:00",
            data_cutoff="2026-07-21T14:55:00+08:00", now=future_now,
        )
    except service.PipelineError as exc:
        assert exc.code == "first_limit_pipeline_future_cutoff"
    else:
        raise AssertionError("future cutoff should fail")


def test_tail_preview_defaults_to_1430_and_starts_at_1430():
    current = datetime(2026, 7, 21, 14, 30, tzinfo=SHANGHAI)
    parameters, _ = service.normalize_parameters(
        trade_date="2026-07-21", stage="tail_preview", now=current,
    )
    assert parameters["as_of"] == "2026-07-21T14:30:00+08:00"
    assert parameters["data_cutoff"] == "2026-07-21T14:30:00+08:00"


def test_default_executor_offline_full_pipeline_has_complete_coverage(
    tmp_path, monkeypatch
):
    _path, connection = database(tmp_path, monkeypatch)
    target = date.fromisoformat(DAY)
    weekdays = []
    current = target - timedelta(days=120)
    while current <= target:
        if current.weekday() < 5:
            weekdays.append(current)
        current += timedelta(days=1)
    d0 = weekdays[-3]

    def daily_record(day):
        if day == d0:
            values = (10, 11, 10, 11, 10, 11)
        elif day > d0:
            previous = 11 if day == weekdays[-2] else 10.8
            close = 10.8 if day == weekdays[-2] else 10.7
            values = (10.6, 10.9, 10.5 if day == weekdays[-2] else 10.4,
                      close, previous, round(previous * 1.1, 2))
        else:
            values = (10, 10.2, 9.9, 10, 10, 11)
        open_, high, low, close, pre_close, upper = values
        return {
            "trade_date": str(day), "open": open_, "high": high, "low": low,
            "close": close, "volume": 100000, "amount": 1000000,
            "pre_close": pre_close, "upper_limit": upper,
            "lower_limit": round(pre_close * 0.9, 2),
        }

    records = {str(day): daily_record(day) for day in weekdays}

    class API:
        def get_trading_dates(self, _exchange, start, end):
            return [
                day for day in weekdays
                if date.fromisoformat(start) <= day <= date.fromisoformat(end)
            ]

        def get_instruments(self, **_kwargs):
            return [{
                "symbol": "SZSE.000001", "sec_type": 1, "sec_name": "Fixture",
                "listed_date": "2020-01-01", "delisted_date": "2038-01-01",
                "board": "MAIN",
            }]

        def get_history_instruments(self, **kwargs):
            start = date.fromisoformat(kwargs["start_date"])
            end = date.fromisoformat(kwargs["end_date"])
            return [
                {
                    "symbol": "SZSE.000001", "trade_date": str(day),
                    "board": "MAIN", "is_suspended": False,
                    "no_price_limit": False, "listed_date": "2020-01-01",
                    "delisted_date": "2038-01-01",
                    "pre_close": records[str(day)]["pre_close"],
                    "upper_limit": records[str(day)]["upper_limit"],
                    "lower_limit": records[str(day)]["lower_limit"],
                }
                for day in weekdays if start <= day <= end
            ]

        def history(self, **kwargs):
            start = date.fromisoformat(kwargs["start_time"][:10])
            end = date.fromisoformat(kwargs["end_time"][:10])
            return [
                records[str(day)] for day in weekdays if start <= day <= end
            ]

    class Provider:
        api = API()

        def list_universe(self, *_args):
            return self.api.get_instruments()

    created = create(connection)
    row = service.execute_job(
        connection, created["job_id"],
        service.DefaultExecutor(Provider()),
    )
    assert row["status"] == "success", {
        "job": dict(row),
        "steps": [dict(item) for item in repo.steps(connection, row["id"])],
    }
    assert row["coverage_complete"] == 1
    coverage = {
        item["domain"]: item for item in repo.coverage(connection, row["id"])
    }
    assert all(item["complete"] for item in coverage.values())
    assert coverage["limit_detection"]["covered_count"] == 7
    assert coverage["daily_bars"]["missing_count"] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM first_limit_events WHERE is_first_limit=1"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM first_limit_pullback_observations"
    ).fetchone()[0] == 1
    candidate = connection.execute(
        "SELECT status,detection_complete FROM daily_candidate_runs"
    ).fetchone()
    assert candidate["detection_complete"] == 1
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
