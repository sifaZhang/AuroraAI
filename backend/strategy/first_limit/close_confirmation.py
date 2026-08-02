"""PR6.13C official-close confirmation and intraday comparison."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
import json

from backend.industry.models import SCORE_VERSION
from backend.industry.service import IndustryService
from .candidate_scoring import candidate_score, industry_environment_score, industry_trend_score


@dataclass(frozen=True)
class OfficialCloseIndustryContext:
    trade_date: date; score_date: date | None; industry_level: int | None
    industry_code: str | None; industry_name: str | None
    official_score: float | None; official_rank: int | None
    official_total: int | None; score_version: str
    confidence: str; status: str; fallback_reason: str | None = None
    def evidence(self): return asdict(self)


@dataclass(frozen=True)
class IndustryEstimationError:
    intraday_estimated_score: float | None; official_close_score: float | None
    score_error: float | None; absolute_score_error: float | None
    intraday_estimated_rank: int | None; official_close_rank: int | None
    rank_error: int | None; intraday_level: int | None; official_level: int | None
    same_level_comparison: bool; intraday_as_of_time: str | None
    official_score_date: date | None; status: str; warnings: tuple[str,...]=()
    def evidence(self): return asdict(self)


@dataclass(frozen=True)
class CandidateConfirmationChange:
    intraday_total_score: float | None; final_total_score: float | None
    total_score_change: float | None; intraday_grade: str | None; final_grade: str | None
    grade_change: str | None; intraday_buy_recommendation: str | None
    final_buy_recommendation: str | None; industry_score_change: float | None
    industry_rank_change: int | None; change_type: str; change_reasons: tuple[str,...]
    def evidence(self): return asdict(self)


def estimation_error(intraday, official):
    same = intraday.get("industry_level") == official.industry_level
    score_error = None if intraday.get("intraday_score") is None or official.official_score is None else intraday["intraday_score"]-official.official_score
    rank_error = None if not same or intraday.get("intraday_rank") is None or official.official_rank is None else official.official_rank-intraday["intraday_rank"]
    return IndustryEstimationError(intraday.get("intraday_score"),official.official_score,score_error,
        abs(score_error) if score_error is not None else None,intraday.get("intraday_rank"),official.official_rank,
        rank_error,intraday.get("industry_level"),official.industry_level,same,str(intraday.get("as_of_time") or "") or None,
        official.score_date,"complete" if score_error is not None else "unavailable",
        () if same else ("different_industry_levels",))


def change_between(intraday_score, final_score, error, pending=False):
    if pending: kind="PENDING"
    elif final_score.grade is None: kind="REMOVED"
    elif intraday_score.get("grade") is None: kind="NEWLY_CONFIRMED"
    else:
        rank={"B":0,"A":1,"S":2}; before=rank[intraday_score["grade"]];after=rank[final_score.grade]
        kind="UPGRADED" if after>before else "DOWNGRADED" if after<before else "UNCHANGED"
    reasons=[]
    if error and error.score_error is not None:
        if error.score_error>0: reasons.append("intraday_industry_score_overestimated")
        if error.score_error<0: reasons.append("intraday_industry_score_underestimated")
    return CandidateConfirmationChange(intraday_score.get("total_score"),None if pending else final_score.total_score,
        None if pending or intraday_score.get("total_score") is None else final_score.total_score-intraday_score["total_score"],
        intraday_score.get("grade"),None if pending else final_score.grade,None if pending else kind,
        intraday_score.get("buy_recommendation"),None if pending else final_score.buy_recommendation,
        None if not error else (None if error.score_error is None else -error.score_error),
        None if not error else error.rank_error,kind,tuple(reasons))


class CloseConfirmationService:
    def __init__(self, connection): self.connection=connection;self.industry=IndustryService(connection)

    def official_context(self, symbol, trade_date):
        day=date.fromisoformat(str(trade_date))
        if not self.industry.is_score_complete(day):
            return OfficialCloseIndustryContext(day,None,None,None,None,None,None,None,SCORE_VERSION,"unavailable","industry_close_score_pending")
        effective=self.industry.get_effective_industry_context(symbol,day)
        return OfficialCloseIndustryContext(day,day,effective.effective_level,effective.effective_industry_code,
            effective.effective_industry_name,effective.effective_score,effective.effective_rank,effective.effective_total,
            SCORE_VERSION,effective.effective_confidence or "unavailable",effective.status,effective.fallback_reason)

    def confirm_snapshot(self, snapshot_id, *, dry_run=False):
        snapshot=self.connection.execute("SELECT * FROM daily_candidate_snapshots WHERE id=?",(snapshot_id,)).fetchone()
        if snapshot is None: raise LookupError("candidate snapshot not found")
        evidence={row[0]:json.loads(row[1]) for row in self.connection.execute(
            "SELECT rule_code,actual_value FROM daily_candidate_evidence WHERE candidate_id=? AND actual_value IS NOT NULL",(snapshot_id,))}
        official=self.official_context(snapshot["symbol"],snapshot["trade_date"])
        intraday=evidence.get("INTRADAY_INDUSTRY_ESTIMATE") or {}
        intraday_score=evidence.get("CANDIDATE_SCORE") or {}
        if official.status=="industry_close_score_pending":
            pending=change_between(intraday_score,candidate_score(0,0,0,0,0,0),None,True)
            return {"status":"pending","official":official,"error":None,"change":pending,"final":None}
        error=estimation_error(intraday,official)
        old_components=intraday_score.get("components") or {}
        previous=(evidence.get("INDUSTRY_CONTEXT") or {}).get("previous_score")
        trend=industry_trend_score(official.official_score,previous,None,official.official_rank,None,official.confidence)
        environment=industry_environment_score(None,previous,official.official_score,trend["score"],official.confidence)
        hard=list(intraday_score.get("hard_exclusions") or [])
        if official.official_score is not None and official.official_score<20: hard.append("OFFICIAL_INDUSTRY_EXTREMELY_WEAK")
        final=candidate_score(old_components.get("shape_pullback",0),old_components.get("first_limit",0),environment["score"],
            old_components.get("capital_activity",0),old_components.get("leader",0),old_components.get("market_risk",0),hard,True)
        change=change_between(intraday_score,final,error)
        status="removed" if final.grade is None else "confirmed"
        payload={"status":status,"official":official,"error":error,"change":change,"final":final,
            "environment":environment,"trend":trend,"plan":next_day_plan(snapshot["symbol"],final)}
        if not dry_run: self._persist(snapshot_id,payload)
        return payload

    def _persist(self,snapshot_id,payload):
        stamp=datetime.now(timezone.utc).isoformat(timespec="seconds")
        final=payload["final"];official=payload["official"];change=payload["change"]
        self.connection.execute("""UPDATE daily_candidate_snapshots SET official_industry_score=?,official_industry_rank=?,
            final_total_score=?,final_candidate_grade=?,final_buy_recommendation=?,confirmation_status=?,
            confirmation_change_type=?,confirmed_at=?,score=?,candidate_grade=?,buy_recommendation=?,updated_at=? WHERE id=?""",
            (official.official_score,official.official_rank,final.total_score,final.grade,final.buy_recommendation,
             payload["status"],change.change_type,stamp,final.total_score,final.grade,final.buy_recommendation,stamp,snapshot_id))
        values=(("OFFICIAL_CLOSE_INDUSTRY",official.evidence()),("INDUSTRY_ESTIMATION_ERROR",payload["error"].evidence()),
            ("CLOSE_CONFIRMATION",{"status":payload["status"],"confirmed_at":stamp,"plan":payload["plan"]}),
            ("CANDIDATE_CHANGE",change.evidence()))
        start=self.connection.execute("SELECT COALESCE(MAX(ordinal),-1)+1 FROM daily_candidate_evidence WHERE candidate_id=?",(snapshot_id,)).fetchone()[0]
        for offset,(code,value) in enumerate(values):
            self.connection.execute("""INSERT INTO daily_candidate_evidence(candidate_id,rule_code,result,actual_value,ordinal)
                VALUES(?,?,?,?,?) ON CONFLICT(candidate_id,rule_code) DO UPDATE SET result=excluded.result,actual_value=excluded.actual_value""",
                (snapshot_id,code,"pass",json.dumps(value,ensure_ascii=False,default=str),start+offset))


def next_day_plan(symbol, final):
    if final.grade not in {"S","A"}: return None
    return {"symbol":symbol,"final_grade":final.grade,"final_total_score":final.total_score,
        "buy_recommendation":final.buy_recommendation,"entry_condition":"沿用首板回调次日入场条件",
        "invalid_condition":"跌破首板回调结构或不可交易","position_suggestion":"重点观察" if final.grade=="S" else "小仓位候选",
        "take_profit_reference":"沿用现有止盈规则","stop_loss_reference":"沿用现有止损规则",
        "next_day_watch_points":["开盘可交易性","回调结构","量价确认"]}
