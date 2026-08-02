"""Read-only PR6.13B diagnostic CLI."""
from __future__ import annotations
import argparse, json
from contextlib import nullcontext
from datetime import date

from backend.expectation_gap.database import connect, connect_readonly, migrate
from .close_confirmation import CloseConfirmationService
from .run_daily_candidates import run_daily_candidates
from .candidate_scoring_service import FirstLimitCandidateScoringService
from .industry_context import build_first_limit_industry_context


def parser():
    value=argparse.ArgumentParser()
    commands=value.add_subparsers(dest="command",required=True)
    score=commands.add_parser("score-candidate")
    score.add_argument("--symbol",required=True);score.add_argument("--trade-date",required=True)
    score.add_argument("--as-of-time",required=True)
    close=commands.add_parser("confirm-close")
    close.add_argument("--trade-date",required=True);close.add_argument("--symbol")
    close.add_argument("--run-id");close.add_argument("--dry-run",action="store_true")
    close.add_argument("--resume",action="store_true");close.add_argument("--force",action="store_true")
    close.add_argument("--output-json",action="store_true");close.add_argument("--output-markdown",action="store_true")
    pipeline=commands.add_parser("run-daily-pipeline")
    pipeline.add_argument("--trade-date",required=True);pipeline.add_argument("--stage",required=True,choices=("intraday","close-confirmation"))
    pipeline.add_argument("--dry-run",action="store_true")
    return value


def main(argv=None):
    args=parser().parse_args(argv)
    if args.command in {"confirm-close","run-daily-pipeline"}:
        if args.command=="run-daily-pipeline" and args.stage=="intraday":
            connection=connect_readonly() if args.dry_run else connect()
            if not args.dry_run:migrate(connection)
            result=run_daily_candidates(connection,trade_date=args.trade_date,stage="tail_preview",
                as_of=f"{args.trade_date}T14:55:00+08:00",data_cutoff=f"{args.trade_date}T14:55:00+08:00",
                strategy_version="first_limit_candidate_score_v2",dry_run=args.dry_run)
            print(json.dumps(result,ensure_ascii=False,default=str,indent=2));return 0
        dry_run=args.dry_run;connection=connect_readonly() if dry_run else connect()
        if not dry_run:migrate(connection)
        symbol=getattr(args,"symbol",None)
        rows=connection.execute("""SELECT id FROM daily_candidate_snapshots WHERE trade_date=? AND stage='tail_preview'
            AND scoring_version='first_limit_candidate_score_v2' AND (? IS NULL OR symbol=?) ORDER BY id""",
            (args.trade_date,symbol.upper() if symbol else None,symbol.upper() if symbol else None)).fetchall()
        service=CloseConfirmationService(connection);results=[]
        for row in rows:
            with connection if not dry_run else nullcontext():
                value=service.confirm_snapshot(row[0],dry_run=dry_run)
            results.append({"candidate_id":row[0],"status":value["status"],"official":value["official"].evidence(),
                "error":value["error"].evidence() if value["error"] else None,
                "change":value["change"].evidence(),"final":value["final"].evidence() if value["final"] else None,
                "next_day_plan":value.get("plan")})
        print(json.dumps({"trade_date":args.trade_date,"dry_run":dry_run,"count":len(results),"results":results},ensure_ascii=False,default=str,indent=2));return 0
    connection=connect_readonly()
    event=connection.execute("""SELECT * FROM first_limit_events WHERE symbol=? AND trade_date<=?
        AND detection_status='detected' AND is_first_limit=1 ORDER BY trade_date DESC LIMIT 1""",
        (args.symbol.upper(),args.trade_date)).fetchone()
    if event is None: raise LookupError("first-limit event not found")
    context=connection.execute("""SELECT * FROM first_limit_context_scores WHERE event_id=?
        AND observation_date<? ORDER BY observation_date DESC LIMIT 1""",(event["id"],args.trade_date)).fetchone()
    industry=build_first_limit_industry_context(connection,event["symbol"],date.fromisoformat(event["trade_date"]),date.fromisoformat(args.trade_date))
    result=FirstLimitCandidateScoringService(connection).score(
        event,dict(context) if context else None,industry,f"{args.trade_date}T{args.as_of_time}:00+08:00")
    print(json.dumps(result.evidence(),ensure_ascii=False,default=str,indent=2));return 0


if __name__ == "__main__": raise SystemExit(main())
