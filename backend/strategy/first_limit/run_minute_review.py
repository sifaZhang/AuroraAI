"""Offline PR6.8 runner and CLI for scoped minute review of PR6.7 trades."""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.expectation_gap.database import connect, connect_readonly, migrate

from .minute_review import (
    MAX_ANALYSIS_SESSIONS,
    VERSION,
    Confirmation,
    MinuteBar,
    confirm_tail_entry,
    evaluate_all_stops,
    indeterminate_stop_results,
    s1_followup_features,
    s1_metrics,
    stop_rule_metrics,
    validate_entry_day_after_confirmation,
)
from .rules import normalize_symbol
from .sync_repository import upsert_minute_bars
from . import minute_review_repository as repo

SHANGHAI = ZoneInfo("Asia/Shanghai")
GROUP_FIELDS = (
    "year",
    "classification",
    "trading_day_offset",
    "board_bucket",
    "protection_type",
    "market_environment",
    "industry_environment",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def normalize_parameters(
    source_run_id, data_cutoff, symbols=None, review_version=VERSION, fetch_missing=False
):
    cutoff = date.fromisoformat(str(data_cutoff))
    canonical = sorted({normalize_symbol(symbol).canonical for symbol in symbols}) if symbols else None
    if review_version != VERSION:
        raise ValueError(f"unsupported minute review version: {review_version}")
    params = {
        "source_backtest_run_id": str(source_run_id),
        "data_cutoff": str(cutoff),
        "symbols": canonical,
        "review_version": review_version,
        "entry_window": "14:40-14:55",
        "analysis_sessions": 3,
        "stop_rules": ["S0", "S1", "S2", "S3", "S4"],
        "fetch_missing": bool(fetch_missing),
    }
    payload = _json(params)
    return params, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _moment(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("cached minute bar must include timezone")
    return parsed.astimezone(SHANGHAI)


def _bar(row):
    return MinuteBar(
        _moment(row["bar_time"]),
        Decimal(str(row["open"])) if row["open"] is not None else None,
        Decimal(str(row["high"])) if row["high"] is not None else None,
        Decimal(str(row["low"])) if row["low"] is not None else None,
        Decimal(str(row["close"])) if row["close"] is not None else None,
        Decimal(str(row["volume"])) if row["volume"] is not None else None,
        Decimal(str(row["amount"])) if row["amount"] is not None else None,
    )


def _window(connection, source, cutoff):
    start = datetime.combine(date.fromisoformat(source["observation_date"]), time(14, 40), SHANGHAI)
    analysis_end = repo.analysis_end_date(
        connection, source["observation_date"], cutoff, MAX_ANALYSIS_SESSIONS
    )
    end = datetime.combine(date.fromisoformat(analysis_end), time(15, 0), SHANGHAI)
    return start, end


def _groups(source):
    try:
        protection = json.loads(source["protection_json"] or "{}").get("mode", "unknown")
    except (TypeError, json.JSONDecodeError):
        protection = "unknown"
    board = source["board_type"]
    board_bucket = "20pct" if board in {"CHINEXT", "STAR"} else "10pct" if board == "MAIN" else "unknown"
    market = (
        "strong" if source["market_score"] is not None and source["market_score"] >= 8
        else "weak" if source["market_score"] is not None and source["market_score"] <= 3
        else "oscillating" if source["market_score"] is not None
        else "unknown"
    )
    industry = (
        "strong" if source["industry_score"] is not None and source["industry_score"] >= 16
        else "weak" if source["industry_score"] is not None and source["industry_score"] <= 8
        else "neutral" if source["industry_score"] is not None
        else "unknown"
    )
    return {
        "year": int(source["observation_date"][:4]),
        "classification": source["classification"],
        "trading_day_offset": source["trading_day_offset"],
        "board_bucket": board_bucket,
        "protection_type": protection if protection in {"P1", "P2"} else "unknown",
        "market_environment": market,
        "industry_environment": industry,
    }


def _cached_bars(connection, source, cutoff):
    start, end = _window(connection, source, cutoff)
    rows = repo.minute_rows(connection, source["symbol"], start.isoformat(), end.isoformat())
    return [_bar(row) for row in rows]


def ensure_scoped_minutes(connection, sources, cutoff, minute_fetcher=None, dry_run=False):
    """Fetch only exact PR6.7 symbol/event windows when an explicit fetcher is supplied."""
    audit = []
    for source in sources:
        start, end = _window(connection, source, cutoff)
        existing = repo.minute_rows(connection, source["symbol"], start.isoformat(), end.isoformat())
        item = {
            "source_trade_id": source["source_trade_id"],
            "symbol": source["symbol"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "cached_count": len(existing),
            "fetched_count": 0,
        }
        if not existing and minute_fetcher is not None and not dry_run:
            values = list(minute_fetcher(source["symbol"], start, end))
            item["fetched_count"] = upsert_minute_bars(connection, values)
        audit.append(item)
    return audit


def gm_minute_fetcher(api):
    """Adapt GM history to the existing minute cache without widening the event window."""
    from backend.collector.sync_first_limit_data import _minute_from_frame
    def fetch(symbol, start, end):
        security = normalize_symbol(symbol)
        frame = api.history(
            symbol=security.gm_symbol,
            frequency="60s",
            start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
            fields="open,high,low,close,volume,amount",
            adjust=0,
            df=True,
        )
        return _minute_from_frame(security, frame)
    return fetch


def review_trade(connection, source, cutoff):
    bars = _cached_bars(connection, source, cutoff)
    window_start, window_end = _window(connection, source, cutoff)
    observation_day = date.fromisoformat(source["observation_date"])
    entry_bars = [bar for bar in bars if bar.moment.date() == observation_day]
    limits = repo.daily_limits(
        connection, source["symbol"], source["observation_date"], cutoff
    )
    upper = limits.get(source["observation_date"], {}).get("upper")
    statuses = repo.daily_statuses(
        connection, source["symbol"], source["observation_date"], cutoff
    )
    entry_status = repo.status_as_of(
        connection, source["symbol"], source["observation_date"]
    )
    if entry_status is not None and entry_status["is_suspended"]:
        confirmation = Confirmation("indeterminate", "suspended_entry_day")
    else:
        confirmation = confirm_tail_entry(entry_bars, source["o0"], upper)
    groups = _groups(source)
    stops = {}
    entry_path_error = None
    followup = {
        "s1_triggered": False,
        "reclaimed_o0": None,
        "same_day_plus_2pct": None,
        "rose_within_3_sessions": None,
    }
    if confirmation.status == "confirmed":
        lower_limits = {day: values["lower"] for day, values in limits.items()}
        entry_path_error = validate_entry_day_after_confirmation(bars, confirmation)
        open_dates = repo.trading_dates(
            connection, source["observation_date"], cutoff, MAX_ANALYSIS_SESSIONS
        )
        expected_sessions = [
            date.fromisoformat(day)
            for day in open_dates
            if not (
                (status := repo.status_as_of(connection, source["symbol"], day))
                and status["is_suspended"]
            )
        ]
        stops = (
            indeterminate_stop_results(entry_path_error)
            if entry_path_error
            else evaluate_all_stops(
                bars,
                confirmation,
                source["o0"],
                lower_limits,
                expected_sessions or None,
            )
        )
        followup = s1_followup_features(
            stops, bars, confirmation, source["o0"], expected_sessions or None
        )
    quality = (
        "indeterminate" if confirmation.status == "indeterminate"
        else "unresolved" if any(result["status"] == "unresolved" for result in stops.values())
        else "indeterminate" if any(result["status"] == "indeterminate" for result in stops.values())
        else "complete"
    )
    audit = {
        "minute_bar_count": len(bars),
        "entry_day_bar_count": len(entry_bars),
        "source": "first_limit_minute_bars",
        "adjustment": "none",
        "data_cutoff": cutoff,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "entry_path_error": entry_path_error,
        "decision_bars_after_exit_excluded": True,
        "followup_analysis": followup,
        "groups": groups,
        "suspended_dates": sorted(
            day for day, value in statuses.items() if value.get("is_suspended")
        ),
    }
    return confirmation, quality, groups, audit, stops


def _load_records(connection, run_id):
    records = []
    for result in connection.execute(
        "SELECT * FROM minute_review_results WHERE run_id=? ORDER BY symbol,event_id", (run_id,)
    ):
        stops = {}
        for stop in connection.execute(
            "SELECT * FROM minute_review_stop_results WHERE review_result_id=?", (result["id"],)
        ):
            stops[stop["stop_rule"]] = {
                key: (Decimal(str(stop[key])) if key in {"net_return", "max_drawdown"} and stop[key] is not None else stop[key])
                for key in stop.keys()
            }
        if len(stops) != 5:
            continue
        audit = json.loads(result["audit_json"])
        records.append({
            "groups": {field: result[field] for field in GROUP_FIELDS},
            "followup": audit.get("followup_analysis", {}),
            "stops": stops,
        })
    return records


def persist_metrics(connection, run_id):
    connection.execute("DELETE FROM minute_review_metrics WHERE run_id=?", (run_id,))
    records = _load_records(connection, run_id)
    overall = s1_metrics(records)
    repo.save_metrics(connection, run_id, "s1", "all", "all", overall)
    for rule in ("S0", "S1", "S2", "S3", "S4"):
        repo.save_metrics(
            connection, run_id, "stop_rule", "stop_rule", rule,
            stop_rule_metrics(records, rule),
        )
    for field in GROUP_FIELDS:
        values = sorted({str(record["groups"][field]) for record in records})
        for value in values:
            subset = [record for record in records if str(record["groups"][field]) == value]
            repo.save_metrics(connection, run_id, "s1_group", field, value, s1_metrics(subset))
    return overall


def run_minute_review(
    connection,
    *,
    source_run_id,
    data_cutoff,
    symbols=None,
    review_version=VERSION,
    run_id=None,
    dry_run=False,
    resume=False,
    force=False,
    minute_fetcher=None,
    fetch_missing=False,
    force_symbols=None,
    failure_hook=None,
    run_failure_hook=None,
):
    params, parameter_hash = normalize_parameters(
        source_run_id, data_cutoff, symbols, review_version,
        fetch_missing=fetch_missing or minute_fetcher is not None,
    )
    source_run = connection.execute(
        "SELECT run_id,status FROM backtest_runs WHERE run_id=?", (source_run_id,)
    ).fetchone()
    if source_run is None:
        raise LookupError("source PR6.7 backtest run not found")
    if source_run["status"] not in {"success", "partial"}:
        raise ValueError("source PR6.7 backtest run must be terminal success or partial")
    sources = repo.source_trades(
        connection, source_run_id, params["symbols"], params["data_cutoff"]
    )
    if dry_run and (run_id or resume or force):
        raise ValueError("dry-run cannot use run_id, resume, or force")
    if force and not resume:
        raise ValueError("force requires resume")
    if force_symbols and not force:
        raise ValueError("force_symbols requires force")
    forced_scope = (
        {normalize_symbol(symbol).canonical for symbol in force_symbols}
        if force_symbols
        else None
    )
    source_symbols = {source["symbol"] for source in sources}
    if forced_scope and not forced_scope.issubset(source_symbols):
        raise ValueError("force_symbols must be within the original run source range")
    processing_sources = (
        [source for source in sources if source["symbol"] in forced_scope]
        if forced_scope
        else sources
    )
    cache_audit = ensure_scoped_minutes(
        connection, processing_sources, params["data_cutoff"], minute_fetcher, dry_run
    )
    if dry_run:
        return {
            "run_id": None,
            "status": "dry_run",
            "planned_count": len(sources),
            "cache_windows": cache_audit,
            "parameters": params,
        }
    selected_run = run_id or f"minute-{uuid.uuid4().hex}"
    with connection:
        if resume:
            if not run_id:
                raise ValueError("resume requires run_id")
            repo.resume_run(connection, selected_run, parameter_hash)
        else:
            repo.create_run(
                connection, selected_run, source_run_id, params,
                parameter_hash, review_version, params["data_cutoff"], len(sources),
            )
    completed = repo.completed_trade_ids(connection, selected_run) if resume else set()
    try:
        if run_failure_hook:
            run_failure_hook("before_items")
        for source in processing_sources:
            trade_id = source["source_trade_id"]
            if trade_id in completed and not force:
                continue
            try:
                with connection:
                    if force:
                        repo.delete_result(connection, selected_run, trade_id)
                    if failure_hook:
                        failure_hook(trade_id, "before_review")
                    confirmation, quality, groups, audit, stops = review_trade(
                        connection, source, params["data_cutoff"]
                    )
                    if failure_hook:
                        failure_hook(trade_id, "before_save")
                    result_id = repo.save_result(
                        connection, selected_run, source, confirmation, quality,
                        groups, audit, stops,
                    )
                    item_status = (
                        "skipped" if confirmation.status == "rejected"
                        else "indeterminate" if quality == "indeterminate"
                        else "unresolved" if quality == "unresolved"
                        else "success"
                    )
                    repo.save_item(
                        connection, selected_run, trade_id, source["symbol"],
                        item_status, result_id,
                    )
            except Exception as exc:
                with connection:
                    repo.save_item(
                        connection, selected_run, trade_id, source["symbol"], "failed", error=exc
                    )
        with connection:
            status = repo.finish_run(connection, selected_run)
            metrics = persist_metrics(connection, selected_run)
        return {
            "run_id": selected_run,
            "status": status,
            "planned_count": len(sources),
            "metrics": metrics,
            "cache_windows": cache_audit,
        }
    except Exception as exc:
        with connection:
            repo.finish_run(connection, selected_run, "failed", exc)
        raise


def export_results(connection, run_id):
    results = []
    for row in connection.execute(
        "SELECT * FROM minute_review_results WHERE run_id=? ORDER BY symbol,event_id", (run_id,)
    ):
        item = dict(row)
        item["audit"] = json.loads(item.pop("audit_json"))
        item["stops"] = [
            dict(stop)
            for stop in connection.execute(
                "SELECT * FROM minute_review_stop_results WHERE review_result_id=? ORDER BY stop_rule",
                (row["id"],),
            )
        ]
        results.append(item)
    metrics = [
        {**dict(row), "metrics": json.loads(row["metrics_json"])}
        for row in connection.execute(
            "SELECT * FROM minute_review_metrics WHERE run_id=? ORDER BY scope,group_key,group_value",
            (run_id,),
        )
    ]
    return _json({"run_id": run_id, "results": results, "metrics": metrics})


def human_report(connection, run_id):
    run = connection.execute("SELECT * FROM minute_review_runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        raise LookupError("minute review run not found")
    overall = connection.execute(
        """SELECT metrics_json FROM minute_review_metrics
           WHERE run_id=? AND scope='s1' AND group_key='all' AND group_value='all'""",
        (run_id,),
    ).fetchone()
    metrics = json.loads(overall[0]) if overall else {}
    return "\n".join([
        f"# PR6.8 分钟复核报告 {run_id}",
        "",
        f"- 状态：{run['status']}",
        f"- PR6.7 来源 run：{run['source_backtest_run_id']}",
        f"- 计划/成功/跳过/不确定/未决/失败：{run['planned_count']}/{run['success_count']}/{run['skipped_count']}/{run['indeterminate_count']}/{run['unresolved_count']}/{run['failure_count']}",
        f"- S1 触发次数：{metrics.get('s1_trigger_count', 0)}",
        f"- S1 平均实际成交损失：{metrics.get('s1_average_actual_loss')}",
        f"- 重新站回 O0 比例：{metrics.get('s1_reclaimed_o0_ratio')}",
        f"- 当天达到 +2% 比例：{metrics.get('s1_same_day_plus_2pct_ratio')}",
        f"- 三个观察交易日内重新上涨比例：{metrics.get('s1_rose_within_3_sessions_ratio')}",
        f"- 相对 S0 最大回撤改善：{metrics.get('s1_max_drawdown_reduction_vs_s0')}",
        f"- 相对其他止损总净收益差：{metrics.get('s1_total_return_delta_vs_other_stops')}",
        "",
        "分钟不足、时间断裂和不可成交结果不会降级为确定成交。",
    ])


def parser():
    result = argparse.ArgumentParser(description="Run PR6.8 minute review from one PR6.7 run")
    result.add_argument("--source-run-id", required=True)
    result.add_argument("--data-cutoff", required=True)
    result.add_argument("--symbols")
    result.add_argument("--review-version", default=VERSION)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--resume", action="store_true")
    result.add_argument("--force", action="store_true")
    result.add_argument("--force-symbols")
    result.add_argument("--run-id")
    result.add_argument("--fetch-missing", action="store_true")
    result.add_argument("--token-env", default="GM_TOKEN")
    result.add_argument("--report", choices=("json", "markdown"), default="json")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        connection = connect_readonly() if args.dry_run else connect()
        if not args.dry_run:
            migrate(connection)
        fetcher = None
        if args.fetch_missing and not args.dry_run:
            from backend.collector.sync_first_limit_data import _load_api
            fetcher = gm_minute_fetcher(_load_api(args.token_env))
        result = run_minute_review(
            connection,
            source_run_id=args.source_run_id,
            data_cutoff=args.data_cutoff,
            symbols=args.symbols.split(",") if args.symbols else None,
            review_version=args.review_version,
            run_id=args.run_id,
            dry_run=args.dry_run,
            resume=args.resume,
            force=args.force,
            minute_fetcher=fetcher,
            fetch_missing=args.fetch_missing,
            force_symbols=args.force_symbols.split(",") if args.force_symbols else None,
        )
        if args.report == "markdown" and result["run_id"]:
            print(human_report(connection, result["run_id"]))
        else:
            payload = dict(result)
            if result["run_id"]:
                payload["report"] = json.loads(export_results(connection, result["run_id"]))
            print(_json(payload))
        return 1 if result["status"] == "partial" else 2 if result["status"] == "failed" else 0
    except (ValueError, LookupError) as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
