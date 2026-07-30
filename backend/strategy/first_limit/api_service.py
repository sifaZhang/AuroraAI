"""Service layer for first-limit candidate queries and safe synchronous runs."""
from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from . import api_repository as api_repo
from . import daily_candidate_repository as candidate_repo
from .api_models import RunRequest
from .daily_candidates import VERSION
from .rules import normalize_symbol
from .run_daily_candidates import (
    DEFAULT_VERSIONS,
    normalize_parameters,
    run_daily_candidates,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
GRADES = {"S", "A", "B"}
CANDIDATE_GRADE_FILTERS = {*GRADES, "none"}
LIFECYCLES = {
    "watching", "eligible", "pending_close_confirmation", "confirmed",
    "eliminated", "expired", "indeterminate",
}
CHANGES = {
    "unchanged", "upgraded", "downgraded", "newly_qualified",
    "eliminated", "preview_missing",
}
RUN_STATUSES = {"running", "success", "partial", "failed"}
ITEM_STATUSES = {"pending", "success", "indeterminate", "skipped", "failed"}
SORTS = set(api_repo.SORT_COLUMNS)
TERMINAL_RUN_STATUSES = {"success", "partial", "failed"}


class FirstLimitAPIError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: dict | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _validate(values, allowed, field):
    selected = tuple(dict.fromkeys(values or ()))
    invalid = [value for value in selected if value not in allowed]
    if invalid:
        raise FirstLimitAPIError(
            422, "first_limit_invalid_parameter",
            f"invalid {field}: {', '.join(invalid)}", {"field": field},
        )
    return selected


def _symbol(value):
    if not value:
        return None
    try:
        return normalize_symbol(value).canonical
    except ValueError as exc:
        raise FirstLimitAPIError(
            422, "first_limit_invalid_parameter", str(exc), {"field": "symbol"}
        ) from exc


def _decode(value):
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise FirstLimitAPIError(
            500, "first_limit_data_error", "stored candidate data is invalid"
        ) from exc
    _finite(decoded)
    return decoded


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise FirstLimitAPIError(
            500, "first_limit_data_error", "stored candidate data is not finite"
        )
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _safe_error(error_type, _message):
    if not error_type:
        return None
    code = re.sub(r"[^A-Za-z0-9_.-]", "_", str(error_type))[:80]
    return f"{code}: candidate evaluation failed"


def _run_summary(row):
    return {
        "run_id": row["run_id"],
        "trade_date": row["trade_date"],
        "stage": row["stage"],
        "as_of": row["as_of"],
        "data_cutoff": row["data_cutoff"],
        "status": row["status"],
        "parameter_hash": row["parameter_hash"],
        "strategy_version": row["strategy_version"],
        "detection_version": row["detection_version"],
        "pullback_version": row["pullback_version"],
        "context_version": row["context_version"],
        "requested_count": row["planned_count"],
        "success_count": row["success_count"],
        "pending_count": row["pending_count"],
        "failed_count": row["failure_count"],
        "confirmed_count": row["confirmed_count"],
        "eliminated_count": row["eliminated_count"],
        "indeterminate_count": row["indeterminate_count"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "error_message": _safe_error(
            "run_failed" if row["last_error"] else None, row["last_error"]
        ),
    }


def list_candidates(
    connection,
    *,
    trade_date,
    stage,
    grades=(),
    lifecycles=(),
    symbol=None,
    change_type=None,
    include_unknown=True,
    sort="grade_rank",
    order="asc",
    limit=100,
    offset=0,
):
    grades = _validate(grades, CANDIDATE_GRADE_FILTERS, "grade")
    lifecycles = _validate(lifecycles, LIFECYCLES, "lifecycle")
    if stage not in {"tail_preview", "close_confirmed"}:
        raise FirstLimitAPIError(
            422, "first_limit_invalid_stage", "invalid candidate stage"
        )
    if change_type:
        _validate((change_type,), CHANGES, "change_type")
        if stage != "close_confirmed":
            raise FirstLimitAPIError(
                422, "first_limit_invalid_parameter",
                "change_type is only valid for close_confirmed",
                {"field": "change_type"},
            )
    if sort not in SORTS or order not in {"asc", "desc"}:
        raise FirstLimitAPIError(
            422, "first_limit_invalid_sort", "invalid candidate sort"
        )
    canonical = _symbol(symbol)
    selected_run = api_repo.latest_run(connection, str(trade_date), stage)
    filters = {
        "grade": list(grades), "lifecycle": list(lifecycles),
        "symbol": canonical, "change_type": change_type,
        "include_unknown": include_unknown, "sort": sort, "order": order,
    }
    if selected_run is None:
        return {
            "items": [], "total": 0, "limit": limit, "offset": offset,
            "filters": filters, "data_date": str(trade_date), "stage": stage,
            "run_id": None, "run_status": None,
        }
    total, rows = api_repo.candidate_page(
        connection, run_id=selected_run["run_id"], grades=grades,
        lifecycles=lifecycles, symbol=canonical, change_type=change_type,
        include_unknown=include_unknown, sort=sort, order=order,
        limit=limit, offset=offset,
    )
    items = []
    for row in rows:
        item = dict(row)
        item.pop("grade_rank", None)
        items.append(item)
    return {
        "items": items,
        "total": total, "limit": limit, "offset": offset, "filters": filters,
        "data_date": str(trade_date), "stage": stage,
        "run_id": selected_run["run_id"], "run_status": selected_run["status"],
    }


def get_candidate(connection, candidate_id):
    row = api_repo.candidate(connection, candidate_id)
    if row is None:
        raise FirstLimitAPIError(
            404, "first_limit_candidate_not_found", "candidate not found"
        )
    run = api_repo.run(connection, row["run_id"])
    evidence = []
    for stored in api_repo.evidence(connection, candidate_id):
        item = dict(stored)
        item["actual_value"] = _decode(item["actual_value"])
        item["threshold_value"] = _decode(item["threshold_value"])
        evidence.append(item)
    return {
        "candidate": dict(row),
        "evidence": evidence,
        "run": _run_summary(run),
    }


def list_runs(
    connection, *, trade_date=None, stage=None, statuses=(),
    strategy_version=None, limit=100, offset=0,
):
    statuses = _validate(statuses, RUN_STATUSES, "status")
    if stage and stage not in {"tail_preview", "close_confirmed"}:
        raise FirstLimitAPIError(
            422, "first_limit_invalid_stage", "invalid candidate stage"
        )
    total, rows = api_repo.run_page(
        connection, trade_date=str(trade_date) if trade_date else None,
        stage=stage, statuses=statuses, strategy_version=strategy_version,
        limit=limit, offset=offset,
    )
    return {
        "items": [_run_summary(row) for row in rows],
        "total": total, "limit": limit, "offset": offset,
        "filters": {
            "trade_date": str(trade_date) if trade_date else None,
            "stage": stage, "status": list(statuses),
            "strategy_version": strategy_version,
        },
    }


def get_run(connection, run_id):
    row = api_repo.run(connection, run_id)
    if row is None:
        raise FirstLimitAPIError(
            404, "first_limit_run_not_found", "run not found"
        )
    items, grades, lifecycles, failures = api_repo.run_groups(connection, run_id)
    return {
        "run": _run_summary(row),
        "item_status_counts": items,
        "grade_counts": grades,
        "lifecycle_counts": lifecycles,
        "failures": [
            {
                "first_limit_event_id": item["first_limit_event_id"],
                "symbol": item["symbol"], "error_code": item["error_type"],
                "error_message": _safe_error(item["error_type"], item["last_error"]),
            }
            for item in failures
        ],
        "terminal": row["status"] in TERMINAL_RUN_STATUSES,
    }


def list_run_items(
    connection, *, run_id, statuses=(), symbol=None, limit=100, offset=0
):
    if api_repo.run(connection, run_id) is None:
        raise FirstLimitAPIError(
            404, "first_limit_run_not_found", "run not found"
        )
    statuses = _validate(statuses, ITEM_STATUSES, "status")
    canonical = _symbol(symbol)
    total, rows = api_repo.item_page(
        connection, run_id=run_id, statuses=statuses, symbol=canonical,
        limit=limit, offset=offset,
    )
    items = []
    for row in rows:
        item = dict(row)
        item["error_code"] = item.pop("error_type")
        item["error_message"] = _safe_error(
            item["error_code"], item.pop("last_error")
        )
        items.append(item)
    return {
        "items": items, "total": total, "limit": limit,
        "offset": offset, "run_id": run_id,
    }


def preview_comparison(
    connection, *, trade_date, symbol=None, change_type=None, grades=(),
    limit=100, offset=0,
):
    grades = _validate(grades, GRADES, "grade")
    if change_type:
        _validate((change_type,), CHANGES, "change_type")
    canonical = _symbol(symbol)
    selected_run = api_repo.latest_run(
        connection, str(trade_date), "close_confirmed"
    )
    if selected_run is None:
        return {
            "items": [], "total": 0, "limit": limit, "offset": offset,
            "trade_date": str(trade_date), "run_id": None,
        }
    total, rows = api_repo.comparison_page(
        connection, run_id=selected_run["run_id"], symbol=canonical,
        change_type=change_type, grades=grades, limit=limit, offset=offset,
    )
    return {
        "items": [dict(row) for row in rows], "total": total,
        "limit": limit, "offset": offset, "trade_date": str(trade_date),
        "run_id": selected_run["run_id"],
    }


def _default_moment(day, stage):
    clock = time(14, 55) if stage == "tail_preview" else time(15, 0)
    return datetime.combine(day, clock, SHANGHAI)


def trigger_run(connection, request: RunRequest):
    evaluated_at = request.as_of or _default_moment(request.trade_date, request.stage)
    cutoff = request.data_cutoff or evaluated_at
    versions = {
        "detection": request.detection_version or DEFAULT_VERSIONS["detection"],
        "pullback": request.pullback_version or DEFAULT_VERSIONS["pullback"],
        "context": request.context_version or DEFAULT_VERSIONS["context"],
    }
    try:
        params, parameter_hash = normalize_parameters(
            trade_date=request.trade_date, stage=request.stage,
            as_of=evaluated_at, data_cutoff=cutoff, symbols=request.symbols,
            strategy_version=request.strategy_version or VERSION,
            versions=versions,
            detect_missing_events=request.detect_missing_events,
        )
    except ValueError as exc:
        raise FirstLimitAPIError(
            422, "first_limit_invalid_run_parameters", str(exc)
        ) from exc
    calendar = connection.execute(
        """SELECT is_open FROM a_share_trading_calendar
           WHERE market='CN' AND trade_date=?""",
        (params["trade_date"],),
    ).fetchone()
    if calendar is None or not calendar["is_open"]:
        raise FirstLimitAPIError(
            422, "first_limit_non_trading_day",
            "trade_date is not an open CN trading day",
        )
    claim_id = f"candidate-{uuid.uuid4().hex}"
    with connection:
        claimed_run, created = candidate_repo.claim_run(
            connection, claim_id, params, parameter_hash
        )
    if not created:
        return {
            "run_id": claimed_run["run_id"], "status": claimed_run["status"],
            "reused": True,
            "poll_url": f"/api/first-limit/runs/{claimed_run['run_id']}",
        }
    try:
        result = run_daily_candidates(
            connection,
            trade_date=params["trade_date"], stage=params["stage"],
            as_of=params["as_of"], data_cutoff=params["data_cutoff"],
            symbols=params["symbols"], strategy_version=params["strategy_version"],
            versions=params["versions"], run_id=claim_id, resume=True, claimed=True,
            detect_missing_events=params["detect_missing_events"],
        )
    except Exception as exc:
        with connection:
            candidate_repo.finish_run(connection, claim_id, "failed", exc)
        raise FirstLimitAPIError(
            500, "first_limit_run_failed", "candidate run failed",
            {"run_id": claim_id},
        ) from exc
    return {
        "run_id": result["run_id"], "status": result["status"], "reused": False,
        "poll_url": f"/api/first-limit/runs/{result['run_id']}",
    }
