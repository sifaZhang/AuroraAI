"""Single local-first refresh pipeline for the Industry Radar."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.data_sources.industry_snapshots import build_industry_daily_snapshots
from backend.data_sources.industry_sync import IndustryRepository, sync_current_industries
from backend.data_sources.registry import build_industry_provider
from backend.collector.sync_a_share_daily_history import build_sync_plans, execute_sync, load_stock_pool
from backend.data_sources.errors import ProviderEmptyDataError, ProviderSchemaError
from .models import SCORE_VERSION
from .score_service import build_industry_scores

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")
CLOSE_READY_TIME = time(15, 10)
_REFRESH_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_STATE: dict[str, object] = {"is_running": False, "run_status": "never_run", "current_step": None,
                              "last_error": None, "last_refresh_at": None}
_CALENDAR_CACHE: dict[str, object] = {"expires_at": None, "value": None}


@dataclass(frozen=True)
class IndustryDateStatus:
    trade_date: date
    node_counts: dict[int, int]
    snapshot_counts: dict[int, int]
    score_counts: dict[int, int]
    complete: bool
    reason: str | None = None


@dataclass(frozen=True)
class IndustryRadarRefreshResult:
    target_trade_date: date | None
    latest_complete_trade_date_before: date | None
    latest_complete_trade_date_after: date | None
    missing_trade_dates: tuple[date, ...]
    processed_dates: tuple[date, ...]
    succeeded_dates: tuple[date, ...]
    failed_dates: tuple[date, ...]
    skipped_dates: tuple[date, ...]
    memberships_refreshed: bool
    daily_bars_fetched: int
    daily_status_fetched: int
    snapshots_written: int
    scores_written: int
    status: str
    dry_run: bool
    forced: bool
    warnings: tuple[str, ...] = ()


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def resolve_target_trade_date_from_calendar(*, now: datetime, open_trade_dates: tuple[date, ...]) -> date | None:
    """Resolve the last completed CN trading day from one Provider calendar response."""
    shanghai_now = now.astimezone(SHANGHAI_TZ) if now.tzinfo else now.replace(tzinfo=SHANGHAI_TZ)
    today = shanghai_now.date()
    eligible = [day for day in open_trade_dates if day < today or (day == today and shanghai_now.time() >= CLOSE_READY_TIME)]
    return max(eligible) if eligible else None


class IndustryRadarRefreshService:
    """Coordinates all refresh entry points without exposing providers to API/UI code."""

    def __init__(self, connection, *, now_factory=lambda: datetime.now(SHANGHAI_TZ),
                 membership_syncer=None, daily_syncer=None, calendar_provider=None):
        self.connection = connection
        self.now_factory = now_factory
        self.membership_syncer = membership_syncer
        self.daily_syncer = daily_syncer
        self.calendar_provider = calendar_provider

    def get_industry_date_status(self, *, trade_date: date, score_version: str = SCORE_VERSION) -> IndustryDateStatus:
        nodes = {int(row[0]): int(row[1]) for row in self.connection.execute(
            "SELECT industry_level,COUNT(*) FROM industry_nodes WHERE classification='SW' AND classification_version='2021' GROUP BY industry_level"
        )}
        snapshots = {int(row[0]): int(row[1]) for row in self.connection.execute(
            "SELECT industry_level,COUNT(*) FROM industry_daily_snapshots WHERE trade_date=? GROUP BY industry_level", (str(trade_date),)
        )}
        scores = {int(row[0]): int(row[1]) for row in self.connection.execute(
            "SELECT industry_level,COUNT(*) FROM industry_daily_scores WHERE trade_date=? AND score_version=? GROUP BY industry_level", (str(trade_date), score_version)
        )}
        if not all(nodes.get(level, 0) and nodes.get(level) == snapshots.get(level, 0) == scores.get(level, 0) for level in (1, 2, 3)):
            return IndustryDateStatus(trade_date, nodes, snapshots, scores, False, "level_count_mismatch")
        duplicate = self.connection.execute(
            "SELECT 1 FROM industry_daily_snapshots WHERE trade_date=? GROUP BY classification,classification_version,industry_code HAVING COUNT(*)>1 LIMIT 1", (str(trade_date),)
        ).fetchone()
        invalid_scores = self.connection.execute(
            "SELECT 1 FROM industry_daily_scores WHERE trade_date=? AND score_version=? AND (total_score<0 OR total_score>100) LIMIT 1", (str(trade_date), score_version)
        ).fetchone()
        ranks = self.connection.execute(
            "SELECT industry_level,MIN(rank_in_level),MAX(rank_in_level),COUNT(*) FROM industry_daily_scores WHERE trade_date=? AND score_version=? GROUP BY industry_level", (str(trade_date), score_version)
        ).fetchall()
        rank_ok = len(ranks) == 3 and all(int(row[1]) == 1 and int(row[2]) == nodes.get(int(row[0])) and int(row[3]) == nodes.get(int(row[0])) for row in ranks)
        reason = "duplicate_snapshots" if duplicate else "invalid_scores" if invalid_scores else None if rank_ok else "rank_range_incomplete"
        return IndustryDateStatus(trade_date, nodes, snapshots, scores, reason is None, reason)

    def _calendar_days(self, start: date, end: date) -> tuple[date, ...]:
        provider = self.calendar_provider or build_industry_provider(provider="tushare")
        try:
            result = provider.list_calendar_days(start_date=start, end_date=end)
        except ProviderEmptyDataError as exc:
            raise ValueError("trading_calendar_empty") from exc
        except ProviderSchemaError as exc:
            raise ValueError("trading_calendar_schema_error") from exc
        except Exception as exc:
            raise ValueError("trading_calendar_unavailable") from exc
        return tuple(sorted(item.trade_date for item in result.data if item.is_open))

    def _calendar_range(self, now: datetime, start_date: date | None) -> tuple[date, date]:
        shanghai_now = now.astimezone(SHANGHAI_TZ)
        end = shanghai_now.date() if shanghai_now.time() >= CLOSE_READY_TIME else shanghai_now.date() - timedelta(days=1)
        if start_date:
            return start_date, end
        row = self.connection.execute("SELECT MIN(trade_date) FROM industry_daily_snapshots").fetchone()
        return (date.fromisoformat(row[0]) if row and row[0] else end - timedelta(days=45)), end

    def latest_complete_trade_date(self, *, through: date | None = None) -> date | None:
        sql = "SELECT DISTINCT trade_date FROM industry_daily_scores WHERE score_version=?"
        params: list[object] = [SCORE_VERSION]
        if through:
            sql += " AND trade_date<=?"; params.append(str(through))
        for row in self.connection.execute(sql + " ORDER BY trade_date DESC", params):
            candidate = date.fromisoformat(row[0])
            if self.get_industry_date_status(trade_date=candidate).complete:
                return candidate
        return None

    def find_missing_industry_dates(self, *, target_trade_date: date, open_trade_dates: tuple[date, ...], start_date: date | None = None,
                                    score_version: str = SCORE_VERSION) -> list[date]:
        latest = self.latest_complete_trade_date(through=target_trade_date)
        if start_date is None:
            start_date = latest or (open_trade_dates[-30] if len(open_trade_dates) >= 30 else open_trade_dates[0])
        return [day for day in open_trade_dates if start_date <= day <= target_trade_date
                if not self.get_industry_date_status(trade_date=day, score_version=score_version).complete]

    def _coverage(self, trade_date: date) -> tuple[int, int]:
        row = self.connection.execute("""SELECT COUNT(DISTINCT m.symbol),COUNT(DISTINCT b.stock_code)
            FROM industry_memberships_current m LEFT JOIN a_share_daily_bars b
            ON b.stock_code=substr(m.symbol,1,6) AND b.trade_date=? AND b.adjustment='none'""", (str(trade_date),)).fetchone()
        return int(row[0]), int(row[1])

    def _sync_missing_daily_data(self, trade_date: date):
        """Delegate incremental collection to the established local-cache collector."""
        stocks = load_stock_pool(self.connection)
        plans = build_sync_plans(self.connection, stocks, mode="incremental", start_date=None,
                                 end_date=trade_date, lookback_days=0)
        return execute_sync(self.connection, plans, workers=2)

    def refresh(self, *, target_trade_date: date | None = None, start_date: date | None = None,
                dry_run: bool = False, force: bool = False, refresh_memberships: bool = False,
                continue_on_error: bool = False) -> IndustryRadarRefreshResult:
        if not _REFRESH_LOCK.acquire(blocking=False):
            return IndustryRadarRefreshResult(None, None, None, (), (), (), (), (), False, 0, 0, 0, 0, "already_running", dry_run, force)
        with _STATE_LOCK:
            _STATE.update(is_running=True, run_status="running", current_step="resolve_target_date", last_error=None)
        try:
            now = self.now_factory()
            calendar_start, calendar_end = self._calendar_range(now, start_date)
            open_days = self._calendar_days(calendar_start, calendar_end)
            target = target_trade_date or resolve_target_trade_date_from_calendar(now=now, open_trade_dates=open_days)
            if target is None:
                return IndustryRadarRefreshResult(None, None, None, (), (), (), (), (), False, 0, 0, 0, 0, "no_work", dry_run, force)
            before = self.latest_complete_trade_date(through=target)
            memberships = int(self.connection.execute("SELECT COUNT(*) FROM industry_memberships_current").fetchone()[0])
            nodes = int(self.connection.execute("SELECT COUNT(*) FROM industry_nodes").fetchone()[0])
            refreshed = False
            if refresh_memberships or not nodes or not memberships:
                if dry_run:
                    refreshed = False
                else:
                    syncer = self.membership_syncer or (lambda: sync_current_industries(provider=build_industry_provider(), repository=IndustryRepository(self.connection)))
                    result = syncer(); refreshed = getattr(result, "status", "success") in {"success", "partial_success"}
                    if not refreshed and not nodes:
                        raise RuntimeError("industry_membership_sync_failed")
            missing = self.find_missing_industry_dates(target_trade_date=target, open_trade_dates=open_days, start_date=start_date)
            if force:
                missing = [day for day in open_days if (start_date or before or target) <= day <= target]
            if not missing:
                return IndustryRadarRefreshResult(target, before, before, (), (), (), (), (), refreshed, 0, 0, 0, 0, "no_work", dry_run, force)
            if dry_run:
                return IndustryRadarRefreshResult(target, before, before, tuple(missing), (), (), (), (), refreshed, 0, 0, 0, 0, "success", True, force)
            processed: list[date] = []; succeeded: list[date] = []; failed: list[date] = []; skipped: list[date] = []; warnings: list[str] = []
            snapshots_written = scores_written = bars_fetched = 0
            for day in missing:
                with _STATE_LOCK: _STATE["current_step"] = f"check_daily_data:{day}"
                expected, covered = self._coverage(day)
                if expected and covered < expected:
                    sync = (self.daily_syncer or self._sync_missing_daily_data)(day)
                    bars_fetched += int(getattr(sync, "affected_rows", 0))
                    expected, covered = self._coverage(day)
                if expected and covered < expected:
                    failed.append(day); warnings.append(f"{day}: daily_data_coverage_insufficient {covered}/{expected}")
                    if not continue_on_error: break
                    continue
                processed.append(day)
                status = self.get_industry_date_status(trade_date=day)
                try:
                    if status.snapshot_counts == status.node_counts and status.score_counts != status.node_counts and not force:
                        score = build_industry_scores(connection=self.connection, trade_date=day, force=False)
                        scores_written += score.scored_count
                    else:
                        snapshot = build_industry_daily_snapshots(connection=self.connection, trade_date=day, force=True)
                        if snapshot.failed_count or snapshot.snapshot_count != sum(status.node_counts.values()):
                            raise RuntimeError("industry_snapshot_incomplete")
                        snapshots_written += snapshot.snapshot_count
                        score = build_industry_scores(connection=self.connection, trade_date=day, force=True)
                        scores_written += score.scored_count
                    if not self.get_industry_date_status(trade_date=day).complete:
                        raise RuntimeError("industry_date_validation_failed")
                    succeeded.append(day)
                except (RuntimeError, ValueError, ArithmeticError, TypeError) as exc:
                    failed.append(day); warnings.append(f"{day}: {type(exc).__name__}: {exc}")
                    if not continue_on_error: break
            after = self.latest_complete_trade_date(through=target)
            outcome = "success" if not failed else "partial_success" if succeeded else "failed"
            return IndustryRadarRefreshResult(target, before, after, tuple(missing), tuple(processed), tuple(succeeded), tuple(failed), tuple(skipped), refreshed, bars_fetched, 0, snapshots_written, scores_written, outcome, False, force, tuple(warnings))
        except (LookupError, ValueError, RuntimeError) as exc:
            with _STATE_LOCK: _STATE.update(run_status="failed", last_error=f"{type(exc).__name__}: {exc}")
            return IndustryRadarRefreshResult(target_trade_date, None, None, (), (), (), (), (), False, 0, 0, 0, 0, "failed", dry_run, force, (f"{type(exc).__name__}: {exc}",))
        finally:
            with _STATE_LOCK:
                if _STATE["run_status"] != "failed": _STATE.update(run_status="success", current_step=None, last_refresh_at=datetime.now(SHANGHAI_TZ).isoformat())
                _STATE["is_running"] = False
            _REFRESH_LOCK.release()

    def refresh_status(self) -> dict[str, object]:
        now = self.now_factory().astimezone(SHANGHAI_TZ)
        try:
            cache_now = datetime.now(SHANGHAI_TZ)
            cached = _CALENDAR_CACHE.get("value") if _CALENDAR_CACHE.get("expires_at") and _CALENDAR_CACHE["expires_at"] > cache_now else None
            if cached is None:
                start, end = self._calendar_range(now, None)
                cached = (start, end, self._calendar_days(start, end))
                _CALENDAR_CACHE.update(value=cached, expires_at=cache_now + timedelta(minutes=10))
            _start, _end, open_days = cached
            target = resolve_target_trade_date_from_calendar(now=now, open_trade_dates=open_days)
            if target is None:
                raise ValueError("trading_calendar_empty")
            latest = self.latest_complete_trade_date(through=target)
            missing = self.find_missing_industry_dates(target_trade_date=target, open_trade_dates=open_days)
            error = None
        except ValueError as exc:
            target = latest = None; missing = []; error = str(exc)
        with _STATE_LOCK: state = dict(_STATE)
        return {"current_shanghai_time": now.isoformat(), "current_auckland_time": now.astimezone(AUCKLAND_TZ).isoformat(),
                "target_trade_date": target, "latest_complete_trade_date": latest, "missing_trade_dates": missing,
                "is_latest": bool(target and latest == target and not missing), "calendar_error": error, **state}
