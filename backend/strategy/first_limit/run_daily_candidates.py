"""PR6.9 offline daily candidate runner, ledger, CLI, and stable reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.expectation_gap.database import connect, connect_readonly, migrate

from . import daily_candidate_repository as repo
from .daily_candidates import VERSION, compare_preview, evaluate_candidate
from .minute_review import MinuteBar, confirm_tail_entry
from .rules import normalize_symbol

SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_VERSIONS = {
    "detection": "first_limit_v1",
    "pullback": "first_limit_pullback_v1",
    "context": "first_limit_context_v1",
}


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _timestamp(value, field):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(SHANGHAI)


def normalize_parameters(
    *,
    trade_date,
    stage,
    as_of=None,
    data_cutoff,
    symbols=None,
    strategy_version=VERSION,
    versions=None,
    detect_missing_events=False,
):
    day = date.fromisoformat(str(trade_date))
    if stage not in {"tail_preview", "close_confirmed"}:
        raise ValueError("stage must be tail_preview or close_confirmed")
    default_time = time(14, 30) if stage == "tail_preview" else time(15, 0)
    evaluated_at = (
        _timestamp(as_of, "as_of")
        if as_of
        else datetime.combine(day, default_time, SHANGHAI)
    )
    cutoff = _timestamp(data_cutoff, "data_cutoff")
    if evaluated_at.date() != day or cutoff.date() != day:
        raise ValueError("as_of and data_cutoff must be on trade_date")
    if cutoff < evaluated_at:
        raise ValueError("data_cutoff must not precede as_of")
    if stage == "tail_preview":
        clock = evaluated_at.timetz().replace(tzinfo=None)
        if not time(14, 30) <= clock <= time(14, 55):
            raise ValueError("tail_preview as_of must be between 14:30 and 14:55")
    elif evaluated_at.timetz().replace(tzinfo=None) < time(15, 0):
        raise ValueError("close_confirmed as_of must be at or after 15:00")
    if strategy_version != VERSION:
        raise ValueError(f"unsupported daily candidate version: {strategy_version}")
    canonical = (
        sorted({normalize_symbol(symbol).canonical for symbol in symbols})
        if symbols else None
    )
    selected_versions = {**DEFAULT_VERSIONS, **(versions or {})}
    params = {
        "trade_date": str(day),
        "stage": stage,
        "as_of": evaluated_at.isoformat(timespec="seconds"),
        "data_cutoff": cutoff.isoformat(timespec="seconds"),
        "symbols": canonical,
        "strategy_version": strategy_version,
        "versions": selected_versions,
        "detect_missing_events": bool(detect_missing_events),
    }
    return params, hashlib.sha256(_json(params).encode("utf-8")).hexdigest()


def _calendar_context(connection, event, trade_date):
    rows = repo.calendar_rows(connection, event["trade_date"], trade_date)
    by_date = {row["trade_date"]: bool(row["is_open"]) for row in rows}
    if event["trade_date"] not in by_date or trade_date not in by_date:
        return None, [], False
    if not by_date[event["trade_date"]] or not by_date[trade_date]:
        return None, [], False
    open_dates = [day for day, is_open in by_date.items() if is_open]
    effective = []
    status_missing = False
    for day in open_dates:
        if day == event["trade_date"]:
            continue
        status = repo.status_as_of(connection, event["symbol"], day)
        if status is None or status["is_suspended"] is None:
            status_missing = True
            effective.append(day)
        elif not status["is_suspended"]:
            effective.append(day)
    return len(effective), [event["trade_date"], *effective], not status_missing


def _decimal(value):
    return Decimal(str(value)) if value is not None else None


def _minute(row):
    moment = _timestamp(row["bar_time"], "minute bar_time")
    return MinuteBar(
        moment,
        _decimal(row["open"]),
        _decimal(row["high"]),
        _decimal(row["low"]),
        _decimal(row["close"]),
        _decimal(row["volume"]),
        _decimal(row["amount"]),
    )


def cached_minute_provider(connection, symbol, start, end):
    for row in repo.minute_rows(
        connection, symbol, start.isoformat(), end.isoformat()
    ):
        yield _minute(row)


def _minute_contiguous(previous, current):
    seconds = (current.moment - previous.moment).total_seconds()
    if seconds == 60:
        return True
    return (
        previous.moment.timetz().replace(tzinfo=None) == time(11, 30)
        and current.moment.timetz().replace(tzinfo=None) in {time(13, 0), time(13, 1)}
    )


def _intraday_bar(minutes, trade_date, as_of):
    values = list(minutes)
    if not values:
        return None, False
    ordered = sorted(values, key=lambda bar: bar.moment)
    complete = (
        ordered[0].moment.timetz().replace(tzinfo=None) == time(9, 30)
        and ordered[-1].moment >= as_of.replace(second=0, microsecond=0)
        and all(
            _minute_contiguous(previous, current)
            for previous, current in zip(ordered, ordered[1:])
        )
    )
    return {
        "trade_date": trade_date,
        "open": ordered[0].open,
        "high": max(bar.high for bar in ordered),
        "low": min(bar.low for bar in ordered),
        "close": ordered[-1].close,
        "volume": sum((bar.volume for bar in ordered), Decimal(0)),
        "amount": sum(
            (bar.amount for bar in ordered if bar.amount is not None), Decimal(0)
        ),
    }, complete


def _event_inputs(connection, event, params, minute_provider):
    trade_date = params["trade_date"]
    observation_day, expected_dates, _status_coverage = _calendar_context(
        connection, event, trade_date
    )
    calendar_available = observation_day is not None
    bars = repo.bars_through(
        connection, event["symbol"], event["trade_date"], trade_date
    )
    tail = None
    minute_count = 0
    minute_complete = None
    if params["stage"] == "tail_preview" and calendar_available and 1 <= observation_day <= 5:
        start = datetime.combine(date.fromisoformat(trade_date), time(9, 30), SHANGHAI)
        end = _timestamp(params["as_of"], "as_of")
        minute_values = list(minute_provider(event["symbol"], start, end))
        minute_count = len(minute_values)
        intraday, minute_complete = _intraday_bar(
            minute_values, trade_date, end
        )
        bars = [bar for bar in bars if bar["trade_date"] < trade_date]
        if intraday is not None:
            if not minute_complete:
                intraday["open"] = None
            bars.append(intraday)
        tail_values = (
            bar for bar in minute_values
            if bar.moment.timetz().replace(tzinfo=None) >= time(14, 40)
        )
        tail = confirm_tail_entry(tail_values, event["open"])
    context = repo.context_for_event(
        connection, event["id"], trade_date,
        params["versions"]["detection"], params["versions"]["pullback"],
        params["versions"]["context"],
        exact_date=params["stage"] == "close_confirmed",
    )
    status = repo.status_as_of(connection, event["symbol"], trade_date)
    return {
        "bars": bars,
        "expected_dates": expected_dates,
        "status": dict(status) if status else None,
        "observation_day": observation_day,
        "context": dict(context) if context else None,
        "tail": tail,
        "calendar_available": calendar_available,
        "minute_count": minute_count,
        "minute_complete": minute_complete,
    }


def review_event(connection, event, params, minute_provider=None):
    from .industry_context import build_first_limit_industry_context

    provider = minute_provider or (
        lambda symbol, start, end: cached_minute_provider(
            connection, symbol, start, end
        )
    )
    inputs = _event_inputs(connection, event, params, provider)
    previous_eliminated = repo.previously_eliminated(
        connection, event["id"], params["trade_date"]
    )
    decision = evaluate_candidate(
        event=dict(event),
        bars=inputs["bars"],
        expected_dates=inputs["expected_dates"],
        status=inputs["status"],
        observation_day=inputs["observation_day"],
        context=inputs["context"],
        stage=params["stage"],
        evaluation_date=params["trade_date"],
        tail_confirmation=inputs["tail"],
        previous_eliminated=previous_eliminated,
        calendar_available=inputs["calendar_available"],
    )
    preview = (
        repo.preview_snapshot(
            connection, event["id"], params["trade_date"],
            params["strategy_version"],
        )
        if params["stage"] == "close_confirmed"
        else None
    )
    change = compare_preview(preview, decision) if params["stage"] == "close_confirmed" else None
    audit = {
        "as_of": params["as_of"],
        "data_cutoff": params["data_cutoff"],
        "daily_bar_count": len(inputs["bars"]),
        "expected_dates": inputs["expected_dates"],
        "minute_bar_count": inputs["minute_count"],
        "minute_coverage_complete": inputs["minute_complete"],
        "context_source_date": (
            inputs["context"].get("observation_date") if inputs["context"] else None
        ),
        "lookahead_check": "providers_bounded_by_as_of_and_data_cutoff",
    }
    industry_context = build_first_limit_industry_context(
        connection,
        event["symbol"],
        date.fromisoformat(event["trade_date"]),
        date.fromisoformat(params["trade_date"]),
    )
    audit["industry_context"] = industry_context.evidence()
    return decision, preview, change, audit


def _detection_complete(connection, params):
    rows = connection.execute(
        """SELECT parameters_json FROM first_limit_sync_runs
           WHERE sync_type='detect' AND status='success'"""
    ).fetchall()
    for row in rows:
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError:
            continue
        if (
            value.get("start_date")
            and value.get("end_date")
            and value.get("start_date") <= params["trade_date"] <= value.get("end_date")
            and value.get("detection_version") == params["versions"]["detection"]
            and (
                params["symbols"] is None
                or set(params["symbols"]).issubset(set(value.get("symbols") or []))
            )
        ):
            return True
    return False


def _run_missing_detection(connection, params, dry_run):
    """Use the existing detector only when the caller explicitly enables it."""
    from .detect_first_limits import _date
    from .detection_runs import run_detection
    from .detector import Bar, Metadata, classify
    from .repository import get_security_status_as_of

    symbols = params["symbols"]
    if symbols is None:
        symbols = [
            row["symbol"]
            for row in connection.execute(
                """SELECT symbol FROM a_share_security_master
                   WHERE is_active=1 ORDER BY symbol"""
            )
        ]
    day = params["trade_date"]

    def decide(symbol, target_day):
        rows = connection.execute(
            """SELECT * FROM a_share_daily_bars
               WHERE stock_code=? AND adjustment='none' AND trade_date<=?
               ORDER BY trade_date DESC LIMIT 21""",
            (symbol.split(".")[0], target_day),
        ).fetchall()
        if not rows or rows[0]["trade_date"] != target_day:
            raise LookupError("missing target daily bar")
        rows = list(reversed(rows))

        def bar(row):
            return Bar(
                _date(row["trade_date"]),
                *(_decimal(row[key]) for key in (
                    "open", "high", "low", "close", "volume", "amount"
                )),
                row["adjustment"],
            )

        def metadata(value):
            return Metadata(
                *(_decimal(value[key]) if value else None for key in (
                    "pre_close", "source_upper_limit", "source_lower_limit"
                ))
            )

        def metadata_row(value_day):
            return connection.execute(
                """SELECT * FROM first_limit_daily_metadata
                   WHERE symbol=? AND trade_date=?""",
                (symbol, value_day),
            ).fetchone()

        target = rows[-1]
        decision = classify(
            symbol, bar(target), metadata(metadata_row(target_day)),
            get_security_status_as_of(connection, symbol, target_day),
            [
                (
                    bar(row), metadata(metadata_row(row["trade_date"])),
                    get_security_status_as_of(connection, symbol, row["trade_date"]),
                )
                for row in rows[:-1]
            ],
        )
        return (
            symbol, target_day, params["versions"]["detection"], decision,
            target["open"], target["high"], target["low"], target["close"],
            metadata_row(target_day)["pre_close"] if metadata_row(target_day) else None,
            str(decision.upper_limit) if decision.upper_limit else None, None,
        )

    detection_params = {
        "start_date": day,
        "end_date": day,
        "symbols": symbols,
        "detection_version": params["versions"]["detection"],
    }
    return run_detection(
        connection, [(symbol, day) for symbol in symbols], detection_params,
        decide, dry_run=dry_run,
    )


def _evaluate_all(connection, events, params, minute_provider=None):
    values = []
    for event in events:
        decision, preview, change, audit = review_event(
            connection, event, params, minute_provider
        )
        values.append({
            "event": dict(event), "decision": decision, "preview": preview,
            "change_type": change, "audit": audit,
        })
    return values


def _relevant_events(connection, events, trade_date):
    """Keep only active D0-D6 events; prior terminal events stop consuming data."""
    selected = []
    for event in events:
        if repo.terminal_before(connection, event["id"], trade_date):
            continue
        observation_day, _expected, _coverage = _calendar_context(
            connection, event, trade_date
        )
        if observation_day is None or observation_day <= 6:
            selected.append(event)
    return selected


def run_daily_candidates(
    connection,
    *,
    trade_date,
    stage,
    data_cutoff,
    as_of=None,
    symbols=None,
    strategy_version=VERSION,
    versions=None,
    run_id=None,
    dry_run=False,
    resume=False,
    force=False,
    force_symbols=None,
    detect_missing_events=False,
    minute_provider=None,
    failure_hook=None,
    run_failure_hook=None,
    claimed=False,
):
    params, parameter_hash = normalize_parameters(
        trade_date=trade_date, stage=stage, as_of=as_of,
        data_cutoff=data_cutoff, symbols=symbols,
        strategy_version=strategy_version, versions=versions,
        detect_missing_events=detect_missing_events,
    )
    if dry_run and (run_id or resume or force or force_symbols):
        raise ValueError("dry-run cannot use run_id, resume, force, or force_symbols")
    if force and not resume:
        raise ValueError("force requires resume")
    if force_symbols and not force:
        raise ValueError("force_symbols requires force")
    if claimed and (not resume or not run_id or force or force_symbols):
        raise ValueError("claimed execution requires a non-force resume with run_id")
    detection_complete = _detection_complete(connection, params)
    detection_result = None
    if not detection_complete and detect_missing_events:
        detection_result = _run_missing_detection(connection, params, dry_run)
        detection_complete = detection_result.get("status") == "success"
    events = repo.source_events(
        connection, params["trade_date"], params["versions"]["detection"],
        params["symbols"],
    )
    events = _relevant_events(connection, events, params["trade_date"])
    forced_scope = (
        {normalize_symbol(symbol).canonical for symbol in force_symbols}
        if force_symbols else None
    )
    if forced_scope and not forced_scope.issubset({event["symbol"] for event in events}):
        raise ValueError("force_symbols must be within the original run event range")

    if dry_run:
        reviewed = _evaluate_all(connection, events, params, minute_provider)
        return {
            "run_id": None, "status": "dry_run", "parameters": params,
            "detection_complete": detection_complete,
            "detection_result": detection_result,
            "results": [decision_payload(value) for value in reviewed],
        }

    existing_hash_run = repo.find_run_by_hash(
        connection, params["trade_date"], params["stage"], parameter_hash
    )
    if resume:
        if not run_id:
            raise ValueError("resume requires run_id")
        selected_run = run_id
        with connection:
            repo.resume_run(connection, selected_run, parameter_hash)
    elif existing_hash_run:
        if run_id and run_id != existing_hash_run["run_id"]:
            raise ValueError("identical formal parameters already belong to another run")
        selected_run = existing_hash_run["run_id"]
        with connection:
            repo.resume_run(connection, selected_run, parameter_hash)
        resume = True
    else:
        selected_run = run_id or f"candidate-{uuid.uuid4().hex}"
        with connection:
            repo.create_run(
                connection, selected_run, params, parameter_hash, len(events),
                detection_complete,
            )
            repo.initialize_items(connection, selected_run, events)

    if resume:
        if claimed:
            with connection:
                repo.initialize_claimed_run(
                    connection, selected_run, events, detection_complete
                )
        frozen_event_ids = repo.scoped_event_ids(connection, selected_run)
        events = [event for event in events if event["id"] in frozen_event_ids]
        if forced_scope and not forced_scope.issubset(
            {event["symbol"] for event in events}
        ):
            raise ValueError("force_symbols must be within the original run event range")
    completed = repo.completed_event_ids(connection, selected_run) if resume else set()
    processing = [
        event for event in events
        if forced_scope is None or event["symbol"] in forced_scope
    ]
    try:
        if run_failure_hook:
            run_failure_hook("before_items")
        for event in processing:
            if event["id"] in completed and not force:
                continue
            try:
                with connection:
                    if force:
                        repo.delete_candidate(connection, selected_run, event["id"])
                    if failure_hook:
                        failure_hook(event["id"], "before_review")
                    decision, preview, change, audit = review_event(
                        connection, event, params, minute_provider
                    )
                    candidate_id = repo.save_candidate(
                        connection, selected_run, event, params["trade_date"],
                        params["stage"], decision, params["versions"],
                        params["strategy_version"], preview, change, audit,
                    )
                    if failure_hook:
                        failure_hook(event["id"], "before_item")
                    item_status = (
                        "indeterminate"
                        if decision.lifecycle_status == "indeterminate"
                        else "skipped" if decision.lifecycle_status == "expired"
                        else "success"
                    )
                    repo.save_item(
                        connection, selected_run, event["id"], event["symbol"],
                        item_status, candidate_id,
                    )
            except Exception as exc:
                with connection:
                    if force:
                        repo.delete_candidate(connection, selected_run, event["id"])
                    repo.save_item(
                        connection, selected_run, event["id"], event["symbol"],
                        "failed", error=exc,
                    )
        with connection:
            status = repo.finish_run(connection, selected_run)
        return {
            "run_id": selected_run, "status": status,
            "planned_count": len(events),
            "detection_complete": detection_complete,
            "detection_result": detection_result,
        }
    except Exception as exc:
        with connection:
            repo.finish_run(connection, selected_run, "failed", exc)
        raise


def _decode(value):
    return json.loads(value) if value is not None else None


def decision_payload(value):
    decision = value["decision"]
    return {
        "symbol": value["event"]["symbol"],
        "first_limit_event_id": value["event"]["id"],
        "observation_day": decision.observation_day,
        "lifecycle_status": decision.lifecycle_status,
        "candidate_grade": decision.candidate_grade,
        "score": decision.score,
        "change_type": value["change_type"],
        "primary_reasons": list(decision.primary_reasons),
        "evidence": [
            {
                "rule_code": item.rule_code, "result": item.result,
                "actual_value": item.actual_value,
                "threshold_value": item.threshold_value, "unit": item.unit,
                "source_date": item.source_date, "source_time": item.source_time,
                "reason_code": item.reason_code,
            }
            for item in decision.evidence
        ],
    }


def export_results(connection, run_id):
    run = repo.run_row(connection, run_id)
    if run is None:
        raise LookupError("daily candidate run not found")
    results = []
    for row in repo.snapshots(connection, run_id):
        item = dict(row)
        item["primary_reasons"] = _decode(item.pop("primary_reasons_json"))
        item["audit"] = _decode(item.pop("audit_json"))
        item["evidence"] = [
            {
                **dict(evidence),
                "actual_value": _decode(evidence["actual_value"]),
                "threshold_value": _decode(evidence["threshold_value"]),
            }
            for evidence in repo.evidence_for(connection, row["id"])
        ]
        results.append(item)
    groups = {
        "candidates": [item for item in results if item["candidate_grade"]],
        "pending": [
            item for item in results
            if item["lifecycle_status"] in {
                "watching", "pending_close_confirmation", "indeterminate"
            }
        ],
        "eliminated": [
            item for item in results
            if item["lifecycle_status"] in {"eliminated", "expired"}
        ],
        "changes": [
            item for item in results if item.get("change_type") is not None
        ],
    }
    return {
        "run": dict(run), "data_completeness": {
            "detection_complete": bool(run["detection_complete"]),
            "indeterminate_count": run["indeterminate_count"],
            "failed_count": run["failure_count"],
        },
        **groups,
        "failed_items": [dict(row) for row in repo.failed_items(connection, run_id)],
    }


def human_report(connection, run_id):
    payload = export_results(connection, run_id)
    run = payload["run"]
    lines = [
        f"# PR6.9 每日候选 {run['trade_date']} / {run['stage']}",
        "",
        f"- run：{run_id}",
        f"- 状态：{run['status']}",
        f"- 数据截止：{run['data_cutoff']}",
        (
            f"- 计划/成功/不确定/跳过/失败：{run['planned_count']}/"
            f"{run['success_count']}/{run['indeterminate_count']}/"
            f"{run['skipped_count']}/{run['failure_count']}"
        ),
        "",
        "## S/A/B 候选",
    ]
    for item in payload["candidates"]:
        lines.append(
            f"- {item['candidate_grade']} {item['symbol']} "
            f"(event {item['first_limit_event_id']}, score {item['score']})"
        )
    if not payload["candidates"]:
        lines.append("- 无")
    lines.extend(["", "## 等待确认 / 不可确定"])
    for item in payload["pending"]:
        lines.append(
            f"- {item['symbol']}：{item['lifecycle_status']} "
            f"({','.join(item['primary_reasons']) or '无原因码'})"
        )
    if not payload["pending"]:
        lines.append("- 无")
    lines.extend(["", "## 淘汰 / 过期"])
    for item in payload["eliminated"]:
        lines.append(
            f"- {item['symbol']}：{item['lifecycle_status']} "
            f"({','.join(item['primary_reasons'])})"
        )
    if not payload["eliminated"]:
        lines.append("- 无")
    lines.extend(["", "## 尾盘至收盘变化"])
    for item in payload["changes"]:
        lines.append(f"- {item['symbol']}：{item['change_type']}")
    if not payload["changes"]:
        lines.append("- 无")
    lines.extend(["", "## 失败项目"])
    for item in payload["failed_items"]:
        lines.append(
            f"- {item['symbol']} / event {item['first_limit_event_id']}："
            f"{item['error_type']}: {item['last_error']}"
        )
    if not payload["failed_items"]:
        lines.append("- 无")
    return "\n".join(lines)


def parser():
    result = argparse.ArgumentParser(description="Run PR6.9 daily candidate pipeline")
    result.add_argument("--trade-date", required=True)
    result.add_argument(
        "--stage", required=True, choices=("tail_preview", "close_confirmed")
    )
    result.add_argument("--as-of")
    result.add_argument("--data-cutoff", required=True)
    result.add_argument("--symbols")
    result.add_argument("--strategy-version", default=VERSION)
    result.add_argument("--detection-version", default=DEFAULT_VERSIONS["detection"])
    result.add_argument("--pullback-version", default=DEFAULT_VERSIONS["pullback"])
    result.add_argument("--context-version", default=DEFAULT_VERSIONS["context"])
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--resume", action="store_true")
    result.add_argument("--force", action="store_true")
    result.add_argument("--force-symbols")
    result.add_argument("--run-id")
    result.add_argument("--detect-missing-events", action="store_true")
    result.add_argument("--report", choices=("json", "markdown"), default="json")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        connection = connect_readonly() if args.dry_run else connect()
        if not args.dry_run:
            migrate(connection)
        result = run_daily_candidates(
            connection,
            trade_date=args.trade_date, stage=args.stage, as_of=args.as_of,
            data_cutoff=args.data_cutoff,
            symbols=args.symbols.split(",") if args.symbols else None,
            strategy_version=args.strategy_version,
            versions={
                "detection": args.detection_version,
                "pullback": args.pullback_version,
                "context": args.context_version,
            },
            run_id=args.run_id, dry_run=args.dry_run, resume=args.resume,
            force=args.force,
            force_symbols=(
                args.force_symbols.split(",") if args.force_symbols else None
            ),
            detect_missing_events=args.detect_missing_events,
        )
        if result["run_id"] and args.report == "markdown":
            print(human_report(connection, result["run_id"]))
        elif result["run_id"]:
            print(_json({**result, "report": export_results(connection, result["run_id"])}))
        else:
            print(_json(result))
        return (
            1 if result["status"] == "partial"
            else 2 if result["status"] == "failed"
            else 0
        )
    except (ValueError, LookupError) as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
