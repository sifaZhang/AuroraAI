import pandas as pd

from backend.collector import probe_sector_data as probe
from backend.expectation_gap.database import connect, migrate
from backend.expectation_gap.refresh_jobs import create_job, refresh_market_pulse_job, run_job


def history():
    return pd.DataFrame({"日期": pd.date_range("2026-06-01", periods=25), "收盘": range(1, 26), "成交量": [10] * 24 + [100]})


class MixedAk:
    def index_realtime_sw(self, symbol):
        if symbol == "二级行业":
            raise KeyError("data")
        return pd.DataFrame({"指数代码": ["801010"], "指数名称": ["农林牧渔"]})

    def index_hist_sw(self, symbol, period):
        return history()

    def stock_board_industry_name_em(self):
        raise ConnectionError("RemoteDisconnected")


class FailedSwAk(MixedAk):
    def index_realtime_sw(self, symbol):
        raise ConnectionError("sw unavailable")


def prepare(monkeypatch, tmp_path):
    db = tmp_path / "jobs.db"
    monkeypatch.setenv("EXPECTATION_DB_URL", f"sqlite:///{db}")
    monkeypatch.setattr(probe, "SW_DELAYS", ())
    monkeypatch.setattr(probe, "EASTMONEY_DELAYS", ())
    connection = connect(db)
    migrate(connection)
    return connection


def test_all_partial_commits_sw_l1_and_updates_progress(tmp_path, monkeypatch):
    connection = prepare(monkeypatch, tmp_path)
    job_id = create_job(connection, "refresh_market_pulse", source="all")
    refresh_market_pulse_job(connection, job_id, source="all", ak=MixedAk())
    job = connection.execute("SELECT * FROM refresh_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "partial"
    assert job["processed"] == 3 and job["total"] == 3 and job["progress_pct"] == 100
    assert connection.execute("SELECT COUNT(*) FROM sector_scores WHERE source='sw_l1'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM sector_scores WHERE source IN ('sw_l2','eastmoney')").fetchone()[0] == 0
    states = dict(connection.execute("SELECT source,status FROM sector_source_status"))
    assert states["sw_l1"] == "healthy"
    assert states["sw_l2"] == states["eastmoney"] == "unavailable"
    connection.close()


def test_all_failed_when_sw_l1_fails(tmp_path, monkeypatch):
    connection = prepare(monkeypatch, tmp_path)
    job_id = create_job(connection, "refresh_market_pulse", source="all")
    refresh_market_pulse_job(connection, job_id, source="all", ak=FailedSwAk())
    job = connection.execute("SELECT * FROM refresh_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert connection.execute("SELECT COUNT(*) FROM sector_scores").fetchone()[0] == 0
    connection.close()


def test_run_job_transitions_pending_running_completed(tmp_path, monkeypatch):
    connection = prepare(monkeypatch, tmp_path)
    job_id = create_job(connection, "refresh_market_pulse", source="sw_l1")
    assert connection.execute("SELECT status FROM refresh_jobs WHERE id=?", (job_id,)).fetchone()[0] == "pending"
    connection.close()
    run_job(job_id, runner_kwargs={"ak": MixedAk()})
    connection = connect(tmp_path / "jobs.db")
    job = connection.execute("SELECT * FROM refresh_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "success"
    assert job["started_at"] and job["finished_at"]
    assert job["processed"] == job["total"] == 1
    connection.close()
