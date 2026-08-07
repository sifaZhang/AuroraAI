"""PR6.12 persistent one-click data preparation and candidate orchestration."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.collector import sync_first_limit_data as sync
from backend.expectation_gap.database import (
    acquire_write_job, connect, migrate, release_write_job,
)

from . import pipeline_repository as repo
from . import daily_candidate_repository as candidate_repo
from .detect_first_limits import detect_first_limits
from .candidate_scoring import VERSION as CANDIDATE_VERSION
from .rules import normalize_symbol
from .run_daily_candidates import normalize_parameters as normalize_candidate_parameters
from .run_daily_candidates import run_daily_candidates
from .run_pullback_observations import run_pullback_observations
from .score_first_limit_context import score_first_limit_context
from .score_first_limit_quality import score_first_limit_quality

SHANGHAI = ZoneInfo("Asia/Shanghai")
UNIVERSE_VERSION = "first_limit_a_share_universe_v1"
DEPENDENCIES = {
    "active_observation_days": 6,
    "first_limit_lookback_days": 20,
    "quality_lookback_days": 20,
    "context_lookback_days": 20,
    "safety_buffer_open_days": 2,
}
STEP_CODES = (
    "calendar", "universe", "security_master", "daily_status", "daily_bars",
    "limit_detection", "quality_scoring", "pullback_observation",
    "market_context", "minute_bars", "candidate_generation",
    "coverage_validation",
)
TERMINAL = {"success", "partial", "failed", "cancelled"}
ACTIVE_THREADS: dict[int, threading.Thread] = {}
_THREAD_GUARD = threading.Lock()


class PipelineError(RuntimeError):
    def __init__(self, code, message, *, status_code=422, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _redact(exc):
    text = str(exc).replace("\\", "/")
    lowered = text.lower()
    if any(word in lowered for word in ("token=", "traceback", "select ", "insert ")):
        return type(exc).__name__
    return f"{type(exc).__name__}: {text}"[:500]


def _audit_output(value):
    """Keep useful counters/ids while stripping provider and environment detail."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"last_error", "error_message"} and item:
                result[key] = _redact(RuntimeError(str(item)))
            elif key == "failures":
                result[key] = [
                    [str(entry[0]), "item_failed"]
                    for entry in (item or ())
                    if isinstance(entry, (list, tuple)) and entry
                ]
            else:
                result[key] = _audit_output(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_audit_output(item) for item in value]
    return value

def _incomplete_completion(output, code):
    """A partial data set is not a failed execution."""
    if not isinstance(output, dict) or output.get("status") != "partial": return output
    if int(output.get("failed", output.get("failed_count", 0)) or 0): return output
    count=sum(int(output.get(key, 0) or 0) for key in ("indeterminate", "missing", "approximate"))
    if not count: return output
    labels={"limit_detection":"无法确定是否首板","quality_scoring":"存在缺失分项","pullback_observation":"存在无法计算的观察项"}
    return {**output,"status":"completed_with_incomplete_data","incomplete_data":{"count":count,"label":labels.get(code,"数据不完整")}}


def _heartbeat_worker(database_path, job_id, stop):
    while not stop.wait(5):
        try:
            connection = sqlite3.connect(database_path, timeout=5)
            try:
                connection.execute(
                    """UPDATE first_limit_pipeline_jobs
                       SET heartbeat_at=? WHERE id=? AND status='running'""",
                    (repo.now(), job_id),
                )
                connection.commit()
            finally:
                connection.close()
        except sqlite3.Error:
            # The foreground step owns error reporting; a transient heartbeat
            # lock must not change business state.
            continue


def _timestamp(value, field):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PipelineError(
            "first_limit_pipeline_invalid_parameter",
            f"{field} must be an ISO-8601 timestamp with timezone",
        ) from exc
    if parsed.tzinfo is None:
        raise PipelineError(
            "first_limit_pipeline_invalid_parameter",
            f"{field} must include timezone",
        )
    return parsed.astimezone(SHANGHAI)


def normalize_parameters(
    *, trade_date, stage, as_of=None, data_cutoff=None, symbols=None,
    now=None, universe_version=UNIVERSE_VERSION,
):
    try:
        day = date.fromisoformat(str(trade_date))
    except ValueError as exc:
        raise PipelineError(
            "first_limit_pipeline_invalid_parameter", "invalid trade_date"
        ) from exc
    current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if day > current.date():
        raise PipelineError(
            "first_limit_pipeline_future_date", "future trade_date is not allowed"
        )
    default_clock = "14:30:00+08:00" if stage == "tail_preview" else "15:00:00+08:00"
    evaluated_at = as_of or f"{day}T{default_clock}"
    cutoff = data_cutoff or evaluated_at
    try:
        normalized, _candidate_hash = normalize_candidate_parameters(
            trade_date=day, stage=stage, as_of=evaluated_at,
            data_cutoff=cutoff, symbols=symbols,
            strategy_version=CANDIDATE_VERSION,
        )
    except ValueError as exc:
        raise PipelineError(
            "first_limit_pipeline_invalid_parameter", str(exc)
        ) from exc
    if _timestamp(normalized["data_cutoff"], "data_cutoff") > current:
        raise PipelineError(
            "first_limit_pipeline_future_cutoff",
            "data_cutoff cannot be later than current time",
        )
    canonical = normalized["symbols"]
    parameters = {
        **normalized,
        "scope": "partial" if canonical else "full_market",
        "universe_version": universe_version,
        "dependencies": DEPENDENCIES,
    }
    digest = hashlib.sha256(_json(parameters).encode("utf-8")).hexdigest()
    return parameters, digest


def plan_required_window(connection, trade_date):
    """Calculate the formal open-day dependency window after calendar bootstrap."""
    target = str(trade_date)
    target_row = connection.execute(
        """SELECT is_open FROM a_share_trading_calendar
           WHERE market='CN' AND trade_date=?""",
        (target,),
    ).fetchone()
    if target_row is None or not target_row[0]:
        raise PipelineError(
            "first_limit_non_trading_day",
            "trade_date is not an open CN trading day",
        )
    required_open_days = (
        DEPENDENCIES["active_observation_days"]
        + DEPENDENCIES["first_limit_lookback_days"]
        + DEPENDENCIES["safety_buffer_open_days"] + 1
    )
    rows = connection.execute(
        """SELECT trade_date FROM a_share_trading_calendar
           WHERE market='CN' AND is_open=1 AND trade_date<=?
           ORDER BY trade_date DESC LIMIT ?""",
        (target, required_open_days),
    ).fetchall()
    if len(rows) < required_open_days:
        raise PipelineError(
            "first_limit_calendar_coverage_incomplete",
            "trading calendar does not cover the required dependency window",
        )
    open_dates = [row[0] for row in reversed(rows)]
    d0_dates = open_dates[-(DEPENDENCIES["active_observation_days"] + 1):]
    return {
        "required_start": open_dates[0],
        "required_end": target,
        "open_dates": open_dates,
        "d0_dates": d0_dates,
        "dependency_open_days": required_open_days,
        "dependencies": DEPENDENCIES,
    }


class GMProvider:
    """Narrow adapter around the existing configured GM client."""
    def __init__(self, token_env="GM_TOKEN"):
        self.api = sync._load_api(token_env)

    def list_universe(self, trade_date, data_cutoff):
        response = self.api.get_instruments(
            exchanges="SHSE,SZSE,BJSE", sec_types=[1],
            skip_suspended=False, skip_st=False, df=False,
        )
        return sync._records(response)


def _universe_record(raw, day):
    symbol_value = sync._field(raw, "symbol")
    try:
        symbol = normalize_symbol(symbol_value).canonical
    except (TypeError, ValueError):
        return None
    listed = sync._field(raw, "listed_date")
    delisted = sync._field(raw, "delisted_date")
    name = str(sync._field(raw, "sec_name", "security_name", "name") or "")
    sec_type = sync._field(raw, "sec_type", "security_type", "instrument_type")
    reason = None
    if not sync._is_target_stock({**raw, "sec_type": sec_type}):
        reason = "not_common_a_share"
    elif listed and sync._parse_date(listed) > day:
        reason = "not_listed"
    elif delisted and sync._parse_date(delisted) <= day:
        reason = "delisted"
    elif bool(sync._field(raw, "is_st")) or "ST" in name.upper() or "退" in name:
        reason = "st_or_delisting"
    elif bool(sync._field(raw, "is_suspended")):
        reason = "suspended"
    return {
        "symbol": symbol, "eligible": reason is None,
        "exclusion_reason": reason, "name": name,
        "listed_date": str(listed)[:10] if listed else None,
        "delisted_date": str(delisted)[:10] if delisted else None,
    }


def local_calendar_plan(connection, trade_date):
    """Return a dependency plan when the locally stored calendar is complete."""
    target = str(trade_date)
    target_row = connection.execute(
        """SELECT is_open FROM a_share_trading_calendar
           WHERE market='CN' AND trade_date=?""",
        (target,),
    ).fetchone()
    if target_row is None:
        return None
    if not target_row[0]:
        return plan_required_window(connection, trade_date)

    required_open_days = (
        DEPENDENCIES["active_observation_days"]
        + DEPENDENCIES["first_limit_lookback_days"]
        + DEPENDENCIES["safety_buffer_open_days"] + 1
    )
    rows = connection.execute(
        """SELECT trade_date FROM a_share_trading_calendar
           WHERE market='CN' AND is_open=1 AND trade_date<=?
           ORDER BY trade_date DESC LIMIT ?""",
        (target, required_open_days),
    ).fetchall()
    if len(rows) < required_open_days:
        return None

    required_start = date.fromisoformat(rows[-1][0])
    stored_days = connection.execute(
        """SELECT COUNT(*) FROM a_share_trading_calendar
           WHERE market='CN' AND trade_date BETWEEN ? AND ?""",
        (str(required_start), target),
    ).fetchone()[0]
    expected_days = (trade_date - required_start).days + 1
    if stored_days != expected_days:
        return None
    return plan_required_window(connection, trade_date)


def minute_scope_symbols(connection, events, trade_date):
    """Return only daily-prequalified stocks whose optimistic score reaches B.

    This intentionally consumes the T0 quality and D1-D5 pullback records,
    never the retired first_limit_context_v1/sector context.  Components that
    need target-day minutes (industry, capital, leader) and market data that is
    not yet available are given their theoretical maxima for this *prefilter*.
    """
    symbols = set()
    for event in events:
        if event["is_one_word_limit"]:
            continue
        quality = connection.execute(
            """SELECT earned_score FROM first_limit_quality_scores
               WHERE event_id=? AND scoring_version='first_limit_quality_v1'
               ORDER BY id DESC LIMIT 1""", (event["id"],)
        ).fetchone()
        pullback = connection.execute(
            """SELECT earned_score,is_eliminated FROM first_limit_pullback_observations
               WHERE event_id=? AND observation_date<=?
               ORDER BY observation_date DESC,id DESC LIMIT 1""",
            (event["id"], str(trade_date)),
        ).fetchone()
        if quality is None or pullback is None or pullback["is_eliminated"]:
            continue
        shape = max(0.0, min(35.0, float(pullback["earned_score"] or 0) / 30.0 * 35.0))
        first = max(0.0, min(20.0, float(quality["earned_score"] or 0)))
        # Unknown target-day industry/capital/leader (35) and market (10) are
        # deliberately optimistic rather than silently treated as zero.
        if shape + first + 35.0 + 10.0 < 65.0:
            continue
        symbols.add(event["symbol"])
    return [normalize_symbol(symbol) for symbol in sorted(symbols)]


@dataclass
class PipelineContext:
    connection: Any
    job_id: int
    parameters: dict
    provider: Any

    @property
    def day(self):
        return date.fromisoformat(self.parameters["trade_date"])

    def plan(self):
        return plan_required_window(self.connection, self.day)

    def symbols(self):
        return [
            normalize_symbol(row["symbol"])
            for row in repo.universe(self.connection, self.job_id)
        ]


class DefaultExecutor:
    """Calls existing formal collectors/runners; it contains no strategy rules."""
    def __init__(self, provider=None):
        self.provider = provider

    def _provider(self):
        if self.provider is None:
            self.provider = GMProvider()
        return self.provider

    def run_step(self, code, context):
        con, params = context.connection, context.parameters
        if code == "calendar":
            plan = local_calendar_plan(con, context.day)
            if plan is not None:
                return {
                    **plan,
                    "planned": 0,
                    "sync": {
                        "status": "skipped",
                        "reason": "local_calendar_complete",
                    },
                }
            provider = self._provider()
            api = getattr(provider, "api", provider)
            # Bootstrap is deliberately calendar-based, then the exact window is
            # derived from formal open-day dependencies.
            bootstrap_start = context.day - timedelta(days=120)
            result = sync.sync_calendar(con, api, bootstrap_start, context.day)
            plan = context.plan()
            return {**plan, "planned": result.planned, "sync": result.__dict__}
        if code == "universe":
            if params["scope"] == "partial":
                records = [
                    {"symbol": symbol, "eligible": True, "exclusion_reason": None}
                    for symbol in params["symbols"]
                ]
            else:
                raw = self._provider().list_universe(
                    context.day, params["data_cutoff"]
                )
                records = [
                    value for value in (
                        _universe_record(record, context.day) for record in raw
                    ) if value is not None
                ]
                if not records:
                    raise PipelineError(
                        "first_limit_universe_empty",
                        "GM returned no verifiable A-share universe",
                    )
            repo.replace_universe(con, context.job_id, records, params["data_cutoff"])
            eligible = sum(bool(row["eligible"]) for row in records)
            return {
                "total_symbols": len(records), "eligible_symbols": eligible,
                "excluded_symbols": len(records) - eligible,
                "scope": params["scope"],
            }
        symbols = context.symbols()
        plan = context.plan()
        start = date.fromisoformat(plan["required_start"])
        target = context.day
        d0_start = date.fromisoformat(plan["d0_dates"][0])
        if code == "security_master":
            provider = self._provider()
            api = getattr(provider, "api", provider)
            missing = sync.plan_security_gaps(con, symbols)
            return sync.sync_securities(con, api, missing).__dict__
        if code == "daily_status":
            provider = self._provider()
            api = getattr(provider, "api", provider)
            gaps = sync.plan_status_gaps(con, symbols, start, target)
            return sync.sync_statuses(
                con, api, symbols, start, target, plans=gaps,
                progress=lambda current, total: repo.progress(
                    con, context.job_id, code, current, total
                ),
            ).__dict__
        if code == "daily_bars":
            provider = self._provider()
            api = getattr(provider, "api", provider)
            gaps = sync.plan_daily_gaps(con, symbols, start, target)
            return sync.sync_daily(
                con, api, gaps,
                progress=lambda current, total: repo.progress(
                    con, context.job_id, code, current, total
                ),
            ).__dict__
        if code == "limit_detection":
            from backend.data_sources.settings import DataSourceSettings
            from backend.data_sources.tushare import TushareClient
            from .tushare_price_limits import load_price_limits
            settings=DataSourceSettings.from_env()
            limits, failures = ({}, {}) if not settings.tushare_token else load_price_limits(TushareClient(settings.tushare_token), [date.fromisoformat(day) for day in plan["d0_dates"]])
            result = detect_first_limits(
                con, start=d0_start, end=target,
                codes=[item.canonical for item in symbols],
                price_limits=limits,
            )
            return {**result, "planned": len(symbols) * len(plan["d0_dates"]), "requested_dates":plan["d0_dates"], "loaded_dates":sorted({str(day) for _,day in limits}), "loaded_rows":len(limits), "failed_dates":{str(day):reason for day,reason in failures.items()}}
        if code == "quality_scoring":
            result = score_first_limit_quality(
                con, start=d0_start, end=target,
                symbols=[item.canonical for item in symbols],
            )
            return {
                **result,
                "planned": sum(result.get(key, 0) for key in (
                    "success", "failed", "skipped"
                )),
            }
        if code == "pullback_observation":
            return run_pullback_observations(
                con, event_start=d0_start, event_end=target,
                through_date=target,
                symbols=[item.canonical for item in symbols],
            )
        if code == "market_context":
            # Retained as a visible historical step only.  New candidates use
            # formal IndustryService context and never first_limit_context_v1.
            return {"status": "skipped", "reason": "legacy_context_excluded", "planned": 0}
        if code == "minute_bars":
            if params["stage"] != "tail_preview":
                return {"status": "skipped", "reason": "close_confirmed"}
            events = con.execute(
                    """SELECT id,symbol,trade_date,is_one_word_limit
                       FROM first_limit_events
                       WHERE trade_date BETWEEN ? AND ?
                         AND detection_status='detected' AND is_first_limit=1
                       ORDER BY symbol""",
                    (str(d0_start), str(target)),
                ).fetchall()
            if not events:
                return {"status": "success", "planned": 0, "rows": 0}
            minute_symbols = minute_scope_symbols(con, events, target)
            if not minute_symbols:
                return {
                    "status": "skipped",
                    "reason": "no_daily_sab_candidates",
                    "planned": 0,
                    "rows": 0,
                }
            provider = self._provider()
            api = getattr(provider, "api", provider)
            return sync.sync_minutes(
                con, api, minute_symbols, target, target, allow_large_run=True
            ).__dict__
        if code == "candidate_generation":
            result = run_daily_candidates(
                con, trade_date=target, stage=params["stage"],
                as_of=params["as_of"], data_cutoff=params["data_cutoff"],
                symbols=[item.canonical for item in symbols],
                strategy_version=CANDIDATE_VERSION,
                run_id=f"candidate-pipeline-{context.job_id}",
                execution_key=f"pipeline:{context.job_id}",
                detect_missing_events=False,
            )
            if params["stage"] == "close_confirmed":
                # Formal close scores are already materialized by the industry
                # pipeline; replace the preview fallback with target-day formal
                # 3 -> 2 -> 1 scoring for every preview snapshot.
                from .close_confirmation import CloseConfirmationService
                rows = con.execute("""SELECT id FROM daily_candidate_snapshots
                    WHERE trade_date=? AND stage='tail_preview'
                      AND scoring_version=? ORDER BY id""", (str(target), CANDIDATE_VERSION)).fetchall()
                service = CloseConfirmationService(con)
                confirmed = [service.confirm_snapshot(row["id"])["status"] for row in rows]
                result["official_close_confirmation"] = {"count": len(confirmed), "statuses": confirmed}
            return result
        if code == "coverage_validation":
            return validate_coverage(context)
        raise ValueError(f"unknown pipeline step: {code}")


def _domain(context, name, expected, covered, plan, details=None):
    missing = max(expected - covered, 0)
    complete = missing == 0
    repo.save_coverage(
        context.connection, context.job_id, name,
        required_start=plan["required_start"], required_end=plan["required_end"],
        expected_count=expected, covered_count=covered, missing_count=missing,
        complete=complete, details=details,
    )
    return complete


def validate_coverage(context):
    con, params, plan = context.connection, context.parameters, context.plan()
    universe_rows = repo.universe(con, context.job_id)
    symbols = [row["symbol"] for row in universe_rows]
    open_dates = plan["open_dates"]
    d0_dates = plan["d0_dates"]
    eligible_count = len(symbols)
    checks = {}
    checks["calendar"] = _domain(
        context, "calendar", len(open_dates), len(open_dates), plan
    )
    checks["universe"] = _domain(
        context, "universe", eligible_count, eligible_count, plan,
        {"scope": params["scope"]},
    )
    expected_pairs = []
    status_expected = 0
    status_covered = 0
    for universe_row in universe_rows:
        source = repo.load(universe_row["source_json"], {})
        listed = source.get("listed_date")
        delisted = source.get("delisted_date")
        for open_day in open_dates:
            if listed and open_day < listed:
                continue
            if delisted and open_day >= delisted:
                continue
            status_expected += 1
            status = con.execute(
                """SELECT is_suspended FROM a_share_security_status_history
                   WHERE symbol=? AND effective_date<=?
                   ORDER BY effective_date DESC LIMIT 1""",
                (universe_row["symbol"], open_day),
            ).fetchone()
            if status is not None:
                status_covered += 1
            if status is not None and status["is_suspended"]:
                continue
            expected_pairs.append((universe_row["symbol"], open_day))
    bars = sum(
        con.execute(
            """SELECT 1 FROM a_share_daily_bars
               WHERE stock_code=? AND adjustment='none' AND trade_date=?""",
            (symbol.split(".")[0], open_day),
        ).fetchone() is not None
        for symbol, open_day in expected_pairs
    )
    checks["daily_status"] = _domain(
        context, "daily_status", status_expected, status_covered, plan,
    )
    checks["daily_bars"] = _domain(
        context, "daily_bars", len(expected_pairs), bars, plan
    )
    detection_expected = eligible_count * len(d0_dates)
    detection_covered = 0
    for run in con.execute(
        """SELECT run_id,parameters_json FROM first_limit_sync_runs
           WHERE sync_type='detect' AND status='success'
           ORDER BY created_at DESC"""
    ):
        value = repo.load(run["parameters_json"], {})
        if (
            value.get("start_date") == d0_dates[0]
            and value.get("end_date") == d0_dates[-1]
            and set(symbols).issubset(set(value.get("symbols") or ()))
        ):
            detection_covered = con.execute(
                """SELECT COUNT(*) FROM first_limit_sync_items
                   WHERE run_id=? AND status='success'""",
                (run["run_id"],),
            ).fetchone()[0]
            break
    checks["detection"] = _domain(
        context, "limit_detection", detection_expected, detection_covered, plan
    )
    placeholders = ",".join("?" for _ in symbols)
    event_sql = """SELECT id,symbol,trade_date,is_one_word_limit FROM first_limit_events
                   WHERE trade_date BETWEEN ? AND ?
                     AND detection_version='first_limit_v1'
                     AND detection_status='detected' AND is_first_limit=1"""
    event_args = [d0_dates[0], d0_dates[-1]]
    if symbols:
        event_sql += f" AND symbol IN ({placeholders})"
        event_args.extend(symbols)
    events = con.execute(event_sql, event_args).fetchall()
    event_count = len(events)
    quality_count = con.execute(
        """SELECT COUNT(*) FROM first_limit_quality_scores q
           JOIN first_limit_events e ON e.id=q.event_id
           WHERE e.trade_date BETWEEN ? AND ?"""
        + (f" AND e.symbol IN ({placeholders})" if symbols else ""),
        [d0_dates[0], d0_dates[-1], *symbols],
    ).fetchone()[0]
    checks["quality"] = _domain(
        context, "quality_scoring", event_count, quality_count, plan
    )
    expected_observations = 0
    for event in events:
        following = [day for day in open_dates if day > event["trade_date"]]
        expected_observations += len(following[1:5])
    observations = con.execute(
        """SELECT COUNT(*) FROM first_limit_pullback_observations
           WHERE first_limit_date BETWEEN ? AND ? AND observation_date<=?"""
        + (f" AND symbol IN ({placeholders})" if symbols else ""),
        [d0_dates[0], d0_dates[-1], params["trade_date"], *symbols],
    ).fetchone()[0]
    checks["pullback"] = _domain(
        context, "pullback_observation", expected_observations, observations, plan
    )
    checks["context"] = _domain(context, "market_context", 0, 0, plan,
        {"status": "skipped", "reason": "legacy_context_excluded"})
    if params["stage"] == "tail_preview":
        minute_symbols = minute_scope_symbols(con, events, context.day)
        cutoff = _timestamp(params["data_cutoff"], "data_cutoff")
        start_stamp = datetime.combine(context.day, datetime.min.time(), SHANGHAI)
        start_stamp = start_stamp.replace(hour=9, minute=30)
        minute_covered = 0
        for security in minute_symbols:
            symbol = security.canonical
            row = con.execute(
                """SELECT MIN(bar_time),MAX(bar_time),COUNT(*)
                   FROM first_limit_minute_bars
                   WHERE symbol=? AND timeframe='1m' AND bar_time BETWEEN ? AND ?""",
                (symbol, start_stamp.isoformat(), cutoff.isoformat()),
            ).fetchone()
            if (
                row[2] and str(row[0])[:16] in {
                    f"{params['trade_date']}T09:30",
                    f"{params['trade_date']}T09:31",
                }
                and str(row[1]).startswith(cutoff.strftime("%Y-%m-%dT%H:%M"))
            ):
                minute_covered += 1
        checks["minutes"] = _domain(
            context, "minute_bars", len(minute_symbols), minute_covered, plan
        )
    candidate = con.execute(
        """SELECT run_id,detection_complete,status FROM daily_candidate_runs
           WHERE trade_date=? AND stage=? ORDER BY created_at DESC LIMIT 1""",
        (params["trade_date"], params["stage"]),
    ).fetchone()
    candidate_complete = bool(
        candidate and candidate["detection_complete"]
        and candidate["status"] in {"success", "partial"}
    )
    checks["candidate"] = _domain(
        context, "candidate_generation", 1, int(candidate_complete), plan,
        {"run_id": candidate["run_id"] if candidate else None},
    )
    complete = params["scope"] == "full_market" and all(checks.values())
    return {
        "status": "success" if complete else "partial",
        "coverage_complete": complete,
        "checks": checks,
        "candidate_run_id": candidate["run_id"] if candidate else None,
    }


def create_job(connection, **values):
    parameters, digest = normalize_parameters(**values)
    if not acquire_write_job(blocking=False):
        raise PipelineError(
            "first_limit_pipeline_database_busy",
            "another data write job is running; wait for it to finish",
            status_code=409,
        )
    try:
        with connection:
            row, created = repo.create_or_reuse(
                connection, parameters, digest, STEP_CODES
            )
            # Only a genuinely pending/running attempt may be reused. Every
            # explicit click after completion starts a new auditable job.
            if not created and row["status"] in {
                "success", "partial", "failed", "cancelled", "interrupted"
            }:
                repo.release_restartable_natural_key(connection, row["id"])
                row, created = repo.create_or_reuse(
                    connection, parameters, digest, STEP_CODES
                )
                if not created:
                    raise RuntimeError("unable to create replacement pipeline job")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise PipelineError(
                "first_limit_pipeline_database_busy",
                "database is busy; retry after the current data job finishes",
                status_code=409,
            ) from exc
        raise
    finally:
        release_write_job()
    return {
        "job_id": row["id"], "status": row["status"],
        "reused": not created,
        "poll_url": f"/api/first-limit/pipeline-jobs/{row['id']}",
    }


def execute_job(connection, job_id, executor=None):
    acquire_write_job(blocking=True)
    try:
        return _execute_job(connection, job_id, executor)
    finally:
        release_write_job()


def _execute_job(connection, job_id, executor=None):
    with connection:
        if not repo.claim(connection, job_id):
            row = repo.job(connection, job_id)
            if row is None:
                raise LookupError("pipeline job not found")
            return dict(row)
    row = repo.job(connection, job_id)
    parameters = repo.load(row["parameter_json"])
    execution = executor or DefaultExecutor()
    provider = getattr(execution, "provider", None)
    context = PipelineContext(connection, job_id, parameters, provider)
    try:
        for step in repo.steps(connection, job_id):
            if repo.job(connection, job_id)["status"] == "cancelled":
                return dict(repo.job(connection, job_id))
            if step["status"] in {"success", "skipped"}:
                continue
            code = step["step_code"]
            with connection:
                repo.start_step(connection, job_id, code)
            database_path = connection.execute("PRAGMA database_list").fetchone()[2]
            heartbeat_stop = threading.Event()
            heartbeat = None
            if database_path:
                heartbeat = threading.Thread(
                    target=_heartbeat_worker,
                    args=(database_path, job_id, heartbeat_stop),
                    name=f"first-limit-heartbeat-{job_id}", daemon=True,
                )
                heartbeat.start()
            try:
                output = _incomplete_completion(execution.run_step(code, context), code)
                if repo.job(connection, job_id)["status"] == "cancelled":
                    return dict(repo.job(connection, job_id))
                failures = output.get("failures") or ()
                if failures:
                    with connection:
                        for key, message in failures:
                            candidate = str(key).split(":", 1)[0]
                            symbol = candidate if "." in candidate and "," not in candidate else None
                            repo.record_failure(
                                connection, job_id, code,
                                "first_limit_pipeline_item_failed",
                                _redact(RuntimeError(str(message))), symbol=symbol,
                            )
                if output.get("status") == "failed":
                    raise PipelineError(
                        "first_limit_pipeline_step_failed",
                        f"{code} failed; inspect step failures",
                        status_code=500,
                    )
                step_status = (
                    "skipped" if output.get("status") == "skipped" else
                    "partial" if output.get("status") in {"partial", "completed_with_incomplete_data"} else "success"
                )
                with connection:
                    total = output.get(
                        "planned", output.get(
                            "planned_count", output.get("total_symbols")
                        )
                    )
                    if total is not None:
                        repo.progress(
                            connection, job_id, code, int(total), int(total)
                        )
                    repo.finish_step(
                        connection, job_id, code, step_status,
                        output=_audit_output(output),
                    )
            except Exception as exc:
                message = _redact(exc)
                code_value = (
                    exc.code if isinstance(exc, PipelineError)
                    else "first_limit_pipeline_step_failed"
                )
                with connection:
                    repo.record_failure(
                        connection, job_id, code, code_value, message
                    )
                    repo.finish_step(
                        connection, job_id, code, "failed",
                        error_code=code_value, error_message=message,
                    )
                    repo.finish_job(
                        connection, job_id, "failed",
                        error_code=code_value, error_message=message,
                    )
                return dict(repo.job(connection, job_id))
            finally:
                heartbeat_stop.set()
                if heartbeat:
                    heartbeat.join(timeout=1)
        coverage_result = repo.load(
            next(
                row["output_summary_json"] for row in repo.steps(connection, job_id)
                if row["step_code"] == "coverage_validation"
            )
        )
        coverage_complete = bool(
            coverage_result["coverage_complete"]
            and parameters["scope"] == "full_market"
        )
        final_status = "success" if coverage_complete else "partial"
        with connection:
            repo.finish_job(
                connection, job_id, final_status,
                candidate_run_id=coverage_result.get("candidate_run_id"),
                coverage_complete=coverage_complete,
            )
        return dict(repo.job(connection, job_id))
    except Exception as exc:
        message = _redact(exc)
        with connection:
            repo.finish_job(
                connection, job_id, "failed",
                error_code="first_limit_pipeline_failed",
                error_message=message,
            )
        return dict(repo.job(connection, job_id))


def _background(job_id):
    acquire_write_job(blocking=True)
    connection = connect()
    try:
        migrate(connection)
        execute_job(connection, job_id)
    finally:
        connection.close()
        release_write_job()
        with _THREAD_GUARD:
            ACTIVE_THREADS.pop(job_id, None)


def start_background(job_id):
    with _THREAD_GUARD:
        existing = ACTIVE_THREADS.get(job_id)
        if existing and existing.is_alive():
            return
        thread = threading.Thread(
            target=_background, args=(job_id,),
            name=f"first-limit-pipeline-{job_id}", daemon=True,
        )
        ACTIVE_THREADS[job_id] = thread
        thread.start()


def recover_jobs(connection, *, start=True):
    with connection:
        repo.recover_stale(connection)
    rows = connection.execute(
        """SELECT id FROM first_limit_pipeline_jobs
           WHERE status='pending' ORDER BY id"""
    ).fetchall()
    if start:
        for row in rows:
            start_background(row["id"])
    return len(rows)


def retry_job(connection, job_id):
    with connection:
        row, changed = repo.prepare_retry(connection, job_id)
    if changed or row["status"] in {"pending", "interrupted"}:
        start_background(job_id)
    return {
        "job_id": job_id, "status": repo.job(connection, job_id)["status"],
        "reused": not changed,
        "poll_url": f"/api/first-limit/pipeline-jobs/{job_id}",
    }


def cancel_job(connection, job_id):
    with connection:
        row, cancelled = repo.cancel(connection, job_id)
    return {
        "job_id": row["id"], "status": row["status"], "cancelled": cancelled,
        "poll_url": f"/api/first-limit/pipeline-jobs/{row['id']}",
    }


def serialize_job(row):
    if row is None:
        return None
    value = dict(row)
    value["coverage_complete"] = bool(value["coverage_complete"])
    value["parameters"] = repo.load(value.pop("parameter_json"), {})
    value.pop("parameter_hash", None)
    if value["status"] == "partial" and not value.get("error_code"):
        value["status"] = "completed_with_incomplete_data"
    return value


def serialize_rows(rows):
    return [serialize_job(row) for row in rows]
