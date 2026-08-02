import json
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.expectation_gap.database import connect, migrate
from backend.strategy.first_limit import api_service
from backend.strategy.first_limit.candidate_scoring import VERSION

NOW = "2026-07-30T07:00:00+00:00"
DAY = "2026-07-30"


def database(tmp_path, monkeypatch):
    path = tmp_path / "first-limit-api.db"
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{path.as_posix()}")
    connection = connect(path)
    migrate(connection)
    connection.execute(
        """INSERT INTO a_share_trading_calendar(
             market,trade_date,is_open,source,quality_flags,updated_at)
           VALUES('CN',?,1,'MANUAL','[]',?)""",
        (DAY, NOW),
    )
    connection.commit()
    return path, connection


def seed_event(connection, symbol, event_date="2026-07-20"):
    return connection.execute(
        """INSERT INTO first_limit_events(
             symbol,exchange,trade_date,detection_version,detection_status,
             is_limit_up_close,touched_upper_limit,is_first_limit,is_one_word_limit,
             is_consecutive_limit,consecutive_limit_days,lookback_trading_days,
             observed_lookback_days,open,high,low,close,exclusion_reasons,
             quality_flags,detected_at,created_at,updated_at)
           VALUES(?,substr(?,8,2),?,'first_limit_v1','detected',1,1,1,0,0,1,
                  20,20,10,11,9.8,11,'[]','[]',?,?,?)""",
        (symbol, symbol, event_date, NOW, NOW, NOW),
    ).lastrowid


def seed_run(connection, run_id, stage, status="success", created_at=NOW):
    params = {
        "trade_date": DAY, "stage": stage,
        "as_of": f"{DAY}T{'14:55' if stage == 'tail_preview' else '15:00'}:00+08:00",
        "data_cutoff": f"{DAY}T{'14:55' if stage == 'tail_preview' else '15:00'}:00+08:00",
    }
    connection.execute(
        """INSERT INTO daily_candidate_runs(
             run_id,trade_date,stage,as_of,data_cutoff,strategy_version,
             detection_version,pullback_version,context_version,parameters_json,
             parameter_hash,detection_complete,status,planned_count,success_count,
             indeterminate_count,skipped_count,failure_count,last_error,
             started_at,finished_at,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, DAY, stage, params["as_of"], params["data_cutoff"], VERSION,
            "first_limit_v1", "first_limit_pullback_v1", "first_limit_context_v1",
            json.dumps(params), f"hash-{run_id}", 1, status, 0, 0, 0, 0, 0,
            None, created_at, created_at if status != "running" else None,
            created_at, created_at,
        ),
    )


def seed_snapshot(
    connection, run_id, event_id, symbol, stage, lifecycle, grade, score,
    *, preview_id=None, change_type=None, ordinal=0,
):
    candidate_id = connection.execute(
        """INSERT INTO daily_candidate_snapshots(
             run_id,first_limit_event_id,trade_date,stage,symbol,observation_day,
             lifecycle_status,candidate_grade,score,preview_candidate_id,change_type,
             detection_version,pullback_version,context_version,strategy_version,
             primary_reasons_json,audit_json,created_at,updated_at)
           VALUES(?,?,?,?,?,2,?,?,?,?,?,'first_limit_v1',
                  'first_limit_pullback_v1','first_limit_context_v1',?,
                  '["REASON"]','{}',?,?)""",
        (
            run_id, event_id, DAY, stage, symbol, lifecycle, grade, score,
            preview_id, change_type, VERSION, NOW, NOW,
        ),
    ).lastrowid
    connection.execute(
        """INSERT INTO daily_candidate_evidence(
             candidate_id,rule_code,result,actual_value,threshold_value,unit,
             source_date,source_time,reason_code,display_text,ordinal)
           VALUES(?,?,?, ?,?,?,?,?,?,?,?)""",
        (
            candidate_id, f"RULE_{ordinal}", "unknown" if grade is None else "pass",
            json.dumps(score), json.dumps(70), "score", DAY, None,
            "REASON", f"evidence {ordinal}", ordinal,
        ),
    )
    return candidate_id


def seed_query_data(connection):
    seed_run(connection, "preview", "tail_preview")
    seed_run(connection, "close", "close_confirmed")
    changes = (
        "unchanged", "upgraded", "downgraded", "newly_qualified",
        "eliminated", "preview_missing",
    )
    close_ids = []
    for index, change in enumerate(changes):
        symbol = f"{index + 1:06d}.SZ"
        event_id = seed_event(connection, symbol)
        preview_id = None
        if change != "preview_missing":
            preview_id = seed_snapshot(
                connection, "preview", event_id, symbol, "tail_preview",
                "eligible", "A", 75 + index, ordinal=20 - index,
            )
        lifecycle = "eliminated" if change == "eliminated" else "confirmed"
        grade = None if change == "eliminated" else ("S" if index % 2 == 0 else "A")
        close_id = seed_snapshot(
            connection, "close", event_id, symbol, "close_confirmed",
            lifecycle, grade, 80 - index, preview_id=preview_id,
            change_type=change, ordinal=index,
        )
        connection.execute(
            """INSERT INTO daily_candidate_items(
                 run_id,first_limit_event_id,symbol,status,candidate_id,attempt,
                 error_type,last_error,started_at,finished_at,updated_at)
               VALUES('close',?,?,?, ?,1,?,?,?, ?,?)""",
            (
                event_id, symbol, "failed" if index == 5 else "success",
                close_id, "RuntimeError" if index == 5 else None,
                "Traceback C:\\secret\\runner.py SQL SELECT token=secret"
                if index == 5 else None,
                NOW, NOW, NOW,
            ),
        )
        close_ids.append(close_id)
    connection.execute(
        """UPDATE daily_candidate_runs SET planned_count=6,success_count=5,
                  failure_count=1,status='partial' WHERE run_id='close'"""
    )
    connection.commit()
    return close_ids


def test_candidate_filters_stable_pagination_detail_and_errors(tmp_path, monkeypatch):
    _path, connection = database(tmp_path, monkeypatch)
    close_ids = seed_query_data(connection)
    connection.close()
    client = TestClient(app)

    first = client.get(
        "/api/first-limit/candidates",
        params={"trade_date": DAY, "stage": "close_confirmed", "limit": 2},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] == 6 and len(body["items"]) == 2
    assert [item["grade"] for item in body["items"]] == ["S", "S"]
    assert body["run_id"] == "close" and body["run_status"] == "partial"
    assert {"intraday_industry_score","official_industry_score","intraday_total_score",
        "final_total_score","confirmation_status","confirmed_at"} <= set(body["items"][0])
    assert body["items"][0]["official_industry_score"] is None
    filtered = client.get(
        "/api/first-limit/candidates",
        params=[
            ("trade_date", DAY), ("stage", "close_confirmed"),
            ("grade", "A,S"), ("lifecycle", "confirmed"),
            ("symbol", "SZSE.000002"),
        ],
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["symbol"] == "000002.SZ"
    assert filtered["items"][0]["base_score"] == 79
    eliminated = client.get(
        "/api/first-limit/candidates",
        params={
            "trade_date": DAY, "stage": "close_confirmed",
            "change_type": "eliminated", "include_unknown": "false",
        },
    ).json()
    assert eliminated["total"] == 1
    assert eliminated["items"][0]["grade"] is None
    assert eliminated["items"][0]["lifecycle"] == "eliminated"
    ungraded = client.get(
        "/api/first-limit/candidates",
        params=[
            ("trade_date", DAY), ("stage", "close_confirmed"),
            ("grade", "none"),
        ],
    ).json()
    assert ungraded["total"] == 1
    assert ungraded["items"][0]["candidate_id"] == eliminated["items"][0]["candidate_id"]
    second_page = client.get(
        "/api/first-limit/candidates",
        params={
            "trade_date": DAY, "stage": "close_confirmed",
            "limit": 2, "offset": 2,
        },
    ).json()
    assert {item["candidate_id"] for item in body["items"]}.isdisjoint(
        item["candidate_id"] for item in second_page["items"]
    )
    detail = client.get(f"/api/first-limit/candidates/{close_ids[0]}")
    assert detail.status_code == 200
    assert detail.json()["evidence"][0]["ordinal"] == 0
    assert detail.json()["evidence"][0]["actual_value"] == 80
    eliminated_detail = client.get(
        f"/api/first-limit/candidates/{eliminated['items'][0]['candidate_id']}"
    ).json()
    assert eliminated_detail["evidence"][0]["result"] == "unknown"
    missing = client.get("/api/first-limit/candidates/999999")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "first_limit_candidate_not_found"
    bad_sort = client.get(
        "/api/first-limit/candidates",
        params={"trade_date": DAY, "stage": "close_confirmed", "sort": "DROP TABLE"},
    )
    assert bad_sort.status_code == 422
    assert bad_sort.json()["error"]["code"] == "first_limit_invalid_sort"


def test_runs_items_comparisons_and_error_redaction(tmp_path, monkeypatch):
    _path, connection = database(tmp_path, monkeypatch)
    seed_query_data(connection)
    connection.close()
    client = TestClient(app)

    runs = client.get("/api/first-limit/runs", params={"trade_date": DAY})
    assert runs.status_code == 200
    assert [item["run_id"] for item in runs.json()["items"]] == ["preview", "close"]
    detail = client.get("/api/first-limit/runs/close").json()
    assert detail["terminal"] is True
    assert detail["run"]["requested_count"] == 6
    assert detail["run"]["failed_count"] == 1
    assert detail["grade_counts"]["unknown"] == 1
    assert "secret" not in json.dumps(detail).lower()
    items = client.get(
        "/api/first-limit/runs/close/items", params={"status": "failed"}
    ).json()
    assert items["total"] == 1
    assert items["items"][0]["error_code"] == "RuntimeError"
    assert "traceback" not in json.dumps(items).lower()
    comparison = client.get(
        "/api/first-limit/preview-comparison", params={"trade_date": DAY}
    ).json()
    assert {item["change_type"] for item in comparison["items"]} == {
        "unchanged", "upgraded", "downgraded", "newly_qualified",
        "eliminated", "preview_missing",
    }
    missing_preview = next(
        item for item in comparison["items"]
        if item["change_type"] == "preview_missing"
    )
    assert missing_preview["preview_candidate_id"] is None
    assert client.get("/api/first-limit/runs/missing").status_code == 404


def test_post_normalizes_defaults_reuses_and_does_not_expose_force(
    tmp_path, monkeypatch
):
    _path, connection = database(tmp_path, monkeypatch)
    connection.close()
    calls = []

    def fake_runner(_connection, **kwargs):
        calls.append(kwargs)
        return {"run_id": kwargs["run_id"], "status": "success"}

    monkeypatch.setattr(api_service, "run_daily_candidates", fake_runner)
    client = TestClient(app)
    payload = {
        "trade_date": DAY, "stage": "tail_preview",
        "symbols": ["SZSE.000002", "000001.SZ", "000002.SZ"],
    }
    first = client.post("/api/first-limit/runs", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["reused"] is False
    assert calls[0]["as_of"] == f"{DAY}T14:30:00+08:00"
    assert calls[0]["data_cutoff"] == f"{DAY}T14:30:00+08:00"
    assert calls[0]["symbols"] == ["000001.SZ", "000002.SZ"]
    replay = client.post(
        "/api/first-limit/runs",
        json={**payload, "symbols": ["000001.SZ", "000002.SZ"]},
    )
    assert replay.status_code == 200
    assert replay.json()["reused"] is True
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert len(calls) == 1
    different = client.post(
        "/api/first-limit/runs",
        json={**payload, "symbols": ["000001.SZ"]},
    )
    assert different.status_code == 200
    assert different.json()["reused"] is False
    assert different.json()["run_id"] != first.json()["run_id"]
    assert len(calls) == 2
    force = client.post(
        "/api/first-limit/runs", json={**payload, "force": True}
    )
    assert force.status_code == 422
    assert force.json()["error"]["code"] == "first_limit_invalid_request"
    assert "SZSE.000002" not in force.text


def test_post_executes_formal_claimed_runner_and_reuses_terminal_run(
    tmp_path, monkeypatch
):
    path, connection = database(tmp_path, monkeypatch)
    connection.close()
    client = TestClient(app)
    payload = {"trade_date": DAY, "stage": "close_confirmed"}
    first = client.post("/api/first-limit/runs", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "success"
    assert first.json()["reused"] is False
    replay = client.post("/api/first-limit/runs", json=payload)
    assert replay.status_code == 200
    assert replay.json()["reused"] is True
    assert replay.json()["run_id"] == first.json()["run_id"]
    checked = connect(path)
    row = checked.execute(
        "SELECT status,planned_count FROM daily_candidate_runs"
    ).fetchone()
    assert tuple(row) == ("success", 0)


def test_concurrent_identical_post_has_one_database_claim_and_execution(
    tmp_path, monkeypatch
):
    path, connection = database(tmp_path, monkeypatch)
    connection.close()
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    call_count = 0

    def fake_runner(_connection, **kwargs):
        nonlocal call_count
        with lock:
            call_count += 1
        entered.set()
        release.wait(timeout=5)
        return {"run_id": kwargs["run_id"], "status": "success"}

    monkeypatch.setattr(api_service, "run_daily_candidates", fake_runner)
    payload = {"trade_date": DAY, "stage": "close_confirmed"}

    def post():
        return TestClient(app).post("/api/first-limit/runs", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(post)
        assert entered.wait(timeout=5)
        second_future = pool.submit(post)
        second = second_future.result(timeout=5)
        release.set()
        first = first_future.result(timeout=5)
    assert first.status_code == second.status_code == 200
    assert {first.json()["reused"], second.json()["reused"]} == {False, True}
    assert first.json()["run_id"] == second.json()["run_id"]
    assert call_count == 1
    checked = connect(path)
    assert checked.execute(
        "SELECT COUNT(*) FROM daily_candidate_runs"
    ).fetchone()[0] == 1


def test_post_time_non_trading_day_and_runner_failure_contract(tmp_path, monkeypatch):
    path, connection = database(tmp_path, monkeypatch)
    connection.close()
    client = TestClient(app)
    invalid = client.post(
        "/api/first-limit/runs",
        json={
            "trade_date": DAY, "stage": "tail_preview",
            "as_of": f"{DAY}T14:55:00+08:00",
            "data_cutoff": f"{DAY}T14:54:00+08:00",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "first_limit_invalid_run_parameters"
    non_trading = client.post(
        "/api/first-limit/runs",
        json={"trade_date": "2026-07-31", "stage": "close_confirmed"},
    )
    assert non_trading.status_code == 422
    assert non_trading.json()["error"]["code"] == "first_limit_non_trading_day"

    def fail_runner(_connection, **_kwargs):
        raise RuntimeError(r"Traceback C:\private\runner.py token=secret")

    monkeypatch.setattr(api_service, "run_daily_candidates", fail_runner)
    failed = client.post(
        "/api/first-limit/runs",
        json={
            "trade_date": DAY, "stage": "close_confirmed",
            "symbols": ["000009.SZ"],
        },
    )
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "first_limit_run_failed"
    assert "secret" not in json.dumps(failed.json()).lower()
    run_id = failed.json()["error"]["details"]["run_id"]
    checked = connect(path)
    row = checked.execute(
        "SELECT status,last_error FROM daily_candidate_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert "controlled" not in (row["last_error"] or "")


def test_openapi_has_first_limit_methods_and_explicit_nullable_fields():
    schema = app.openapi()
    assert set(schema["paths"]["/api/first-limit/runs"]) == {"get", "post"}
    assert "/api/first-limit/preview-comparison" in schema["paths"]
    candidate = schema["components"]["schemas"]["Candidate"]
    assert "grade" in candidate["properties"]


def test_migrate_repairs_legacy_candidate_run_missing_detection_complete(tmp_path):
    path = tmp_path / "legacy-candidate-run.db"
    connection = connect(path)
    migrate(connection)
    connection.execute(
        "ALTER TABLE daily_candidate_runs DROP COLUMN detection_complete"
    )
    connection.commit()
    assert "detection_complete" not in {
        row[1] for row in connection.execute("PRAGMA table_info(daily_candidate_runs)")
    }
    migrate(connection)
    columns = {
        row[1]: row for row in connection.execute(
            "PRAGMA table_info(daily_candidate_runs)"
        )
    }
    assert columns["detection_complete"][3] == 1
    assert columns["detection_complete"][4] == "0"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
