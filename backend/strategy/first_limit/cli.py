"""Read-only PR6.13B diagnostic CLI."""
from __future__ import annotations
import argparse, json
from datetime import date

from backend.expectation_gap.database import connect_readonly
from .candidate_scoring_service import FirstLimitCandidateScoringService
from .industry_context import build_first_limit_industry_context


def parser():
    value=argparse.ArgumentParser()
    commands=value.add_subparsers(dest="command",required=True)
    score=commands.add_parser("score-candidate")
    score.add_argument("--symbol",required=True);score.add_argument("--trade-date",required=True)
    score.add_argument("--as-of-time",required=True)
    return value


def main(argv=None):
    args=parser().parse_args(argv);connection=connect_readonly()
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
