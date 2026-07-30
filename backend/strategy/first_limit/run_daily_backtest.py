"""Offline PR6.7 daily-proxy runner, ledger, CLI, and stable export."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal

from backend.expectation_gap.database import connect, connect_readonly, migrate

from .backtest_metrics import portfolio_summary
from .backtest_repository import candidates, record_exit_delay, record_exit_signal, resolve_exit
from .daily_backtest import Bar, VERSION, entry, exit_trade, returns
from .rules import normalize_symbol


DEFAULT_VERSIONS = {
    "detection": "first_limit_v1",
    "quality": "first_limit_quality_v1",
    "pullback": "first_limit_pullback_v1",
    "context": "first_limit_context_v1",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def normalize_parameters(
    *,
    start_date,
    end_date,
    data_cutoff,
    symbols,
    strategy_version=VERSION,
    versions=None,
):
    start = date.fromisoformat(str(start_date))
    end = date.fromisoformat(str(end_date))
    cutoff = date.fromisoformat(str(data_cutoff))
    if end < start:
        raise ValueError("end_date must not precede start_date")
    if cutoff < end:
        raise ValueError("data_cutoff must not precede end_date")
    canonical = sorted({normalize_symbol(symbol).canonical for symbol in symbols})
    if not canonical:
        raise ValueError("at least one symbol is required")
    selected_versions = {**DEFAULT_VERSIONS, **(versions or {})}
    if strategy_version != VERSION:
        raise ValueError(f"unsupported strategy version: {strategy_version}")
    params = {
        "start_date": str(start),
        "end_date": str(end),
        "data_cutoff": str(cutoff),
        "symbols": canonical,
        "strategy_version": strategy_version,
        "versions": selected_versions,
        "backtest_scope": "daily_proxy",
    }
    payload = _json(params)
    return params, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bars(connection, symbol: str, start_date: str, cutoff: str):
    rows = connection.execute(
        """SELECT b.trade_date,b.open,b.high,b.low,b.close,b.volume,b.amount,
                  m.source_upper_limit,m.source_lower_limit,
                  COALESCE((SELECT s.is_suspended FROM a_share_security_status_history s
                            WHERE s.symbol=? AND s.effective_date<=b.trade_date
                            ORDER BY s.effective_date DESC LIMIT 1),0) is_suspended
           FROM a_share_daily_bars b
           LEFT JOIN first_limit_daily_metadata m ON m.symbol=? AND m.trade_date=b.trade_date
           WHERE b.stock_code=? AND b.adjustment='none' AND b.trade_date BETWEEN ? AND ?
           ORDER BY b.trade_date""",
        (symbol, symbol, symbol.split(".")[0], start_date, cutoff),
    ).fetchall()
    for row in rows:
        yield Bar(
            row["trade_date"],
            Decimal(str(row["open"])) if row["open"] is not None else None,
            Decimal(str(row["high"])) if row["high"] is not None else None,
            Decimal(str(row["low"])) if row["low"] is not None else None,
            Decimal(str(row["close"])) if row["close"] is not None else None,
            Decimal(str(row["volume"])) if row["volume"] is not None else None,
            Decimal(str(row["amount"])) if row["amount"] is not None else None,
            Decimal(str(row["source_upper_limit"])) if row["source_upper_limit"] is not None else None,
            Decimal(str(row["source_lower_limit"])) if row["source_lower_limit"] is not None else None,
            bool(row["is_suspended"]),
        )


def _insert_signal(connection, run_id, candidate, strategy_version):
    now = _now()
    return connection.execute(
        """INSERT INTO backtest_signals(
             run_id,event_id,observation_id,symbol,first_limit_date,observation_date,trading_day_offset,
             detection_version,scoring_version,pullback_version,context_scoring_version,backtest_version,
             daily_base_score,signal_status,signal_available_at,approximate_entry,lookahead_check)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id,event_id) DO NOTHING""",
        (
            run_id,
            candidate["event_id"],
            candidate["observation_id"],
            candidate["symbol"],
            candidate["first_limit_date"],
            candidate["observation_date"],
            candidate["trading_day_offset"],
            candidate["detection_version"],
            candidate["scoring_version"],
            candidate["pullback_version"],
            candidate["context_scoring_version"],
            strategy_version,
            candidate["daily_base_score"],
            "accepted",
            candidate["observation_date"],
            1,
            "cutoff_enforced",
        ),
    ).lastrowid or connection.execute(
        "SELECT id FROM backtest_signals WHERE run_id=? AND event_id=?",
        (run_id, candidate["event_id"]),
    ).fetchone()[0]


def _insert_trade(connection, signal_id, entered):
    now = _now()
    return connection.execute(
        """INSERT INTO backtest_trades(
             signal_id,entry_status,entry_reason,actual_entry_date,entry_price_raw,entry_price,shares,entry_cost,
             exit_status,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(signal_id) DO NOTHING""",
        (
            signal_id,
            entered["status"],
            entered.get("reason"),
            entered.get("date"),
            float(entered["raw"]) if entered.get("raw") is not None else None,
            float(entered["price"]) if entered.get("price") is not None else None,
            entered.get("shares"),
            float(entered["cost"]) if entered.get("cost") is not None else None,
            "pending" if entered["status"] == "filled" else "unfilled",
            now,
            now,
        ),
    ).lastrowid or connection.execute(
        "SELECT id FROM backtest_trades WHERE signal_id=?", (signal_id,)
    ).fetchone()[0]


def _resolve_dates(exit_result, exit_bars):
    delay = exit_result.get("exit_delay_market_days", 0)
    if exit_result.get("status") == "closed":
        final_index = next(i for i, bar in enumerate(exit_bars) if bar.date == exit_result["date"])
        signal_index = final_index - delay
        return exit_bars[signal_index].date, exit_bars[signal_index + 1 : final_index + 1]
    if delay:
        return exit_bars[-delay - 1].date, exit_bars[-delay:]
    return exit_bars[-1].date if exit_bars else None, []


def run_symbol_backtest(connection, run_id, symbol, params, *, failure_hook=None):
    """Execute one symbol. The caller owns the surrounding transaction."""
    rows = candidates(
        connection,
        params["start_date"],
        params["end_date"],
        params["versions"],
        [symbol],
    )
    counts = Counter(trade_count=0, closed_count=0, unresolved_count=0, skipped_count=0)
    signal_ids = []
    for candidate in rows:
        if failure_hook:
            failure_hook(symbol, "before_signal")
        bars = _bars(connection, symbol, candidate["observation_date"], params["data_cutoff"])
        observation = next(bars, None)
        if observation is not None and observation.date != candidate["observation_date"]:
            observation = None
        signal_id = _insert_signal(connection, run_id, candidate, params["strategy_version"])
        signal_ids.append(signal_id)
        entered = entry(observation)
        trade_id = _insert_trade(connection, signal_id, entered)
        counts["trade_count"] += 1
        if entered["status"] != "filled":
            counts["skipped_count"] += 1
            continue
        consumed_bars = []
        def observed_bars():
            for bar in bars:
                consumed_bars.append(bar)
                yield bar
        result = exit_trade(entered, observed_bars(), candidate["first_open"])
        signal_date, delay_bars = _resolve_dates(result, consumed_bars)
        if signal_date is None:
            counts["skipped_count"] += 1
            continue
        original_reason = result["reason"].removesuffix("_delayed")
        record_exit_signal(connection, trade_id, signal_date, original_reason)
        for number, delay_bar in enumerate(delay_bars, 1):
            recovered = result.get("status") == "closed" and delay_bar.date == result.get("date")
            record_exit_delay(
                connection,
                trade_id,
                number,
                delay_bar.date,
                "sellable_recovery_day" if recovered else "untradable_daily_bar",
                "filled" if recovered else "pending",
            )
        if failure_hook:
            failure_hook(symbol, "before_resolve")
        if result["status"] == "closed":
            persisted_result = {
                **result,
                "raw": float(result["raw"]),
                "price": float(result["price"]),
            }
            calculated_returns = {
                key: float(value) for key, value in returns(entered, result).items()
            }
            resolve_exit(connection, trade_id, persisted_result, calculated_returns)
            counts["closed_count"] += 1
        else:
            unresolved = {
                "status": "open_unresolved",
                "reason": result["reason"],
                "exit_delay_market_days": result.get("exit_delay_market_days", 0),
            }
            resolve_exit(connection, trade_id, unresolved)
            counts["unresolved_count"] += 1
        connection.execute(
            """UPDATE backtest_trades SET holding_days=?,mfe=?,mae=?,intraday_path_ambiguous=?,updated_at=?
               WHERE id=?""",
            (
                result.get("holding_days"),
                float(result["mfe"]) if result.get("mfe") is not None else None,
                float(result["mae"]) if result.get("mae") is not None else None,
                int(bool(result.get("intraday_path_ambiguous"))),
                _now(),
                trade_id,
            ),
        )
    return {"symbol": symbol, "signal_ids": signal_ids, **counts}


def _save_item(connection, run_id, symbol, status, result=None, error=None):
    now = _now()
    result = result or {}
    error_text = str(error)[:1000] if error else None
    error_type = type(error).__name__ if error else None
    connection.execute(
        """INSERT INTO backtest_run_items(
             run_id,item_key,symbol,status,trade_count,closed_count,unresolved_count,skipped_count,
             signal_id,result_json,error_type,last_error,started_at,finished_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id,item_key) DO UPDATE SET
             status=excluded.status,trade_count=excluded.trade_count,closed_count=excluded.closed_count,
             unresolved_count=excluded.unresolved_count,skipped_count=excluded.skipped_count,
             signal_id=excluded.signal_id,result_json=excluded.result_json,error_type=excluded.error_type,
             last_error=excluded.last_error,finished_at=excluded.finished_at,updated_at=excluded.updated_at""",
        (
            run_id,
            symbol,
            symbol,
            status,
            result.get("trade_count", 0),
            result.get("closed_count", 0),
            result.get("unresolved_count", 0),
            result.get("skipped_count", 0),
            (result.get("signal_ids") or [None])[0],
            _json(result) if result else None,
            error_type,
            error_text,
            now,
            now,
            now,
        ),
    )


def _finish_run(connection, run_id, *, forced_status=None, error=None):
    counts = connection.execute(
        """SELECT COUNT(*) planned,
                  SUM(status='success') success,SUM(status='skipped') skipped,SUM(status='failed') failed,
                  COALESCE(SUM(unresolved_count),0) unresolved
           FROM backtest_run_items WHERE run_id=?""",
        (run_id,),
    ).fetchone()
    planned = counts["planned"]
    failed = counts["failed"] or 0
    status = forced_status or ("failed" if planned and failed == planned else "partial" if failed else "success")
    now = _now()
    connection.execute(
        """UPDATE backtest_runs SET status=?,planned_count=?,success_count=?,skipped_count=?,
                  failure_count=?,unresolved_count=?,last_error=?,finished_at=?,updated_at=? WHERE run_id=?""",
        (
            status,
            planned,
            counts["success"] or 0,
            counts["skipped"] or 0,
            failed,
            counts["unresolved"] or 0,
            str(error)[:1000] if error else None,
            now,
            now,
            run_id,
        ),
    )
    return status


def _create_run(connection, run_id, params, parameter_hash):
    now = _now()
    connection.execute(
        """INSERT INTO backtest_runs(
             run_id,parameters_json,parameter_hash,status,backtest_version,backtest_scope,detection_version,
             start_date,end_date,data_cutoff_date,symbols_json,is_dry_run,started_at,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            _json(params),
            parameter_hash,
            "running",
            params["strategy_version"],
            "daily_proxy",
            params["versions"]["detection"],
            params["start_date"],
            params["end_date"],
            params["data_cutoff"],
            _json(params["symbols"]),
            0,
            now,
            now,
            now,
        ),
    )


def run_backtest(
    connection,
    *,
    start_date,
    end_date,
    data_cutoff,
    symbols,
    strategy_version=VERSION,
    versions=None,
    run_id=None,
    dry_run=False,
    resume=False,
    force=False,
    failure_hook=None,
    run_failure_hook=None,
):
    params, parameter_hash = normalize_parameters(
        start_date=start_date,
        end_date=end_date,
        data_cutoff=data_cutoff,
        symbols=symbols,
        strategy_version=strategy_version,
        versions=versions,
    )
    if dry_run and (resume or force or run_id):
        raise ValueError("dry-run cannot be combined with run_id, resume, or force")
    if force and not resume:
        raise ValueError("force requires resume")
    if dry_run:
        results = []
        for symbol in params["symbols"]:
            rows = candidates(connection, params["start_date"], params["end_date"], params["versions"], [symbol])
            results.append({"symbol": symbol, "candidate_count": len(rows)})
        return {"run_id": None, "status": "dry_run", "parameters": params, "items": results}
    if resume and not run_id:
        raise ValueError("resume requires run_id")
    selected_run_id = run_id or f"daily-{uuid.uuid4().hex}"
    if resume:
        existing = connection.execute(
            "SELECT parameter_hash,status FROM backtest_runs WHERE run_id=?", (selected_run_id,)
        ).fetchone()
        if existing is None:
            raise LookupError("run not found")
        if existing["parameter_hash"] != parameter_hash:
            raise ValueError("resume parameters do not match original run")
        with connection:
            connection.execute(
                "UPDATE backtest_runs SET status='running',finished_at=NULL,last_error=NULL,updated_at=? WHERE run_id=?",
                (_now(), selected_run_id),
            )
    else:
        with connection:
            _create_run(connection, selected_run_id, params, parameter_hash)
    try:
        if run_failure_hook:
            run_failure_hook("before_items")
        for symbol in params["symbols"]:
            previous = connection.execute(
                "SELECT status FROM backtest_run_items WHERE run_id=? AND symbol=?",
                (selected_run_id, symbol),
            ).fetchone()
            if previous and previous["status"] == "success" and not force:
                continue
            try:
                with connection:
                    if force:
                        connection.execute(
                            "DELETE FROM backtest_signals WHERE run_id=? AND symbol=?",
                            (selected_run_id, symbol),
                        )
                    result = run_symbol_backtest(
                        connection,
                        selected_run_id,
                        symbol,
                        params,
                        failure_hook=failure_hook,
                    )
                    item_status = "skipped" if result["trade_count"] == 0 else "success"
                    _save_item(connection, selected_run_id, symbol, item_status, result)
            except Exception as exc:
                with connection:
                    _save_item(connection, selected_run_id, symbol, "failed", error=exc)
        with connection:
            status = _finish_run(connection, selected_run_id)
            summary = persist_portfolio_summary(connection, selected_run_id)
        return {"run_id": selected_run_id, "status": status, "portfolio": summary}
    except Exception as exc:
        with connection:
            _finish_run(connection, selected_run_id, forced_status="failed", error=exc)
        raise


def persist_portfolio_summary(connection, run_id):
    trades = [
        dict(row)
        for row in connection.execute(
            """SELECT t.* FROM backtest_trades t
               JOIN backtest_signals s ON s.id=t.signal_id WHERE s.run_id=? ORDER BY s.symbol,s.event_id""",
            (run_id,),
        )
    ]
    summary = portfolio_summary(trades)
    connection.execute("DELETE FROM backtest_metrics WHERE run_id=? AND scope='portfolio'", (run_id,))
    for key, value in summary.items():
        metric_value = float(value) if isinstance(value, Decimal) else value
        if isinstance(metric_value, (dict, list)):
            connection.execute(
                "INSERT INTO backtest_metrics(run_id,scope,metric_key,metric_value,details_json) VALUES(?,?,?,?,?)",
                (run_id, "portfolio", key, None, _json(metric_value)),
            )
        else:
            connection.execute(
                "INSERT INTO backtest_metrics(run_id,scope,metric_key,metric_value,details_json) VALUES(?,?,?,?,?)",
                (run_id, "portfolio", key, metric_value, "{}"),
            )
    return summary


def export_run(connection, run_id, output_format="json"):
    rows = [
        dict(row)
        for row in connection.execute(
            """SELECT s.symbol,s.event_id,t.entry_status,t.actual_entry_date,t.entry_price,
                      t.terminal_status,t.unresolved_reason,t.actual_exit_date,t.exit_price,
                      t.exit_delay_market_days,t.gross_return,t.net_return
               FROM backtest_signals s JOIN backtest_trades t ON t.signal_id=s.id
               WHERE s.run_id=? ORDER BY s.symbol,s.event_id""",
            (run_id,),
        )
    ]
    if output_format == "json":
        return _json(rows)
    if output_format != "csv":
        raise ValueError("output_format must be json or csv")
    output = io.StringIO(newline="")
    fields = list(rows[0]) if rows else [
        "symbol", "event_id", "entry_status", "actual_entry_date", "entry_price",
        "terminal_status", "unresolved_reason", "actual_exit_date", "exit_price",
        "exit_delay_market_days", "gross_return", "net_return",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def parser():
    result = argparse.ArgumentParser(description="Run the offline PR6.7 daily-proxy backtest")
    result.add_argument("--start-date", required=True)
    result.add_argument("--end-date", required=True)
    result.add_argument("--data-cutoff", required=True)
    result.add_argument("--symbols", required=True)
    result.add_argument("--strategy-version", default=VERSION)
    result.add_argument("--detection-version", default=DEFAULT_VERSIONS["detection"])
    result.add_argument("--quality-version", default=DEFAULT_VERSIONS["quality"])
    result.add_argument("--pullback-version", default=DEFAULT_VERSIONS["pullback"])
    result.add_argument("--context-version", default=DEFAULT_VERSIONS["context"])
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--resume", action="store_true")
    result.add_argument("--run-id")
    result.add_argument("--force", action="store_true")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        versions = {
            "detection": args.detection_version,
            "quality": args.quality_version,
            "pullback": args.pullback_version,
            "context": args.context_version,
        }
        kwargs = {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "data_cutoff": args.data_cutoff,
            "symbols": args.symbols.split(","),
            "strategy_version": args.strategy_version,
            "versions": versions,
            "run_id": args.run_id,
            "dry_run": args.dry_run,
            "resume": args.resume,
            "force": args.force,
        }
        connection = connect_readonly() if args.dry_run else connect()
        if not args.dry_run:
            migrate(connection)
        result = run_backtest(connection, **kwargs)
        print(_json(result))
        return 1 if result["status"] == "partial" else 2 if result["status"] == "failed" else 0
    except (ValueError, LookupError) as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
