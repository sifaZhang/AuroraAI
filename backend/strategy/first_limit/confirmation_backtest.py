"""Data-boundary helpers for PR6.13C historical confirmation analysis."""
from __future__ import annotations
from datetime import datetime, time


def validate_intraday_backtest(minutes, cutoff):
    cutoff_value=datetime.fromisoformat(str(cutoff))
    visible=[row for row in minutes if datetime.fromisoformat(str(row["bar_time"]))<=cutoff_value]
    if not visible or cutoff_value.time().replace(tzinfo=None) not in {time(14,30),time(14,55)}:
        return {"status":"intraday_not_backtestable","bars":[]}
    return {"status":"complete","bars":visible,"data_cutoff":cutoff_value.isoformat()}


def validate_close_backtest(official_score_date, simulated_time):
    moment=datetime.fromisoformat(str(simulated_time))
    if moment.time().replace(tzinfo=None)<time(15,0):
        raise ValueError("official close industry score is unavailable before simulated close")
    if str(official_score_date)!=moment.date().isoformat():
        raise ValueError("official score date must equal simulated close date")
    return True


def confirmation_metrics(rows):
    total=len(rows);changed=sum(row.get("intraday_grade")!=row.get("final_grade") for row in rows)
    over=sum((row.get("score_error") or 0)>0 for row in rows)
    return {"count":total,"change_rate":changed/total if total else None,
        "industry_overestimate_rate":over/total if total else None}
