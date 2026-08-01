from __future__ import annotations
import json
from statistics import mean
from .models import IndustryScore, SCORE_VERSION

EPSILON=1e-9; VOLUME_UP=1.20; VOLUME_DOWN=0.80

def clamp(value, low=0.0, high=1.0): return min(high,max(low,float(value)))
def percentile(value, values):
    valid=sorted(float(v) for v in values if v is not None)
    if value is None or not valid: return 0.0
    if len(valid)==1: return 1.0
    return sum(v < float(value) for v in valid)/(len(valid)-1)

def activity_state(ret, ratio, history_ok):
    if not history_ok or ret is None or ratio is None: return "history_insufficient"
    price="up" if ret>EPSILON else "down" if ret < -EPSILON else "flat"
    volume="up" if ratio>=VOLUME_UP else "down" if ratio<=VOLUME_DOWN else "neutral"
    if price=="flat" or volume=="neutral": return "neutral"
    return f"volume_{volume}_price_{price}"

ACTIVITY={"history_insufficient":7.5,"volume_up_price_up":15.0,"volume_down_price_up":8.0,
          "volume_up_price_down":0.0,"volume_down_price_down":6.0,"neutral":7.5}

def score_cross_section(snapshots, histories, version=SCORE_VERSION):
    ew=[s["equal_weight_return"] for s in snapshots]; med=[s["median_return"] for s in snapshots]
    provisional=[]
    for s in snapshots:
        history=histories.get(s["industry_code"],[])
        amounts=[x["turnover_amount"] for x in history if x["turnover_amount"] is not None]
        med_amounts=[x["median_turnover_amount"] for x in history if x["median_turnover_amount"] is not None]
        ratio=lambda current, vals, days: (current/mean(vals[-days:]) if current is not None and len(vals)>=days and mean(vals[-days:]) else None)
        r5=ratio(s["turnover_amount"],amounts,5); r20=ratio(s["turnover_amount"],amounts,20)
        mr20=ratio(s["median_turnover_amount"],med_amounts,20); history_days=len(amounts)
        strength=percentile(s["equal_weight_return"],ew)*15+percentile(s["median_return"],med)*10
        direction=5 if (s["median_return"] or 0)>EPSILON else 0 if (s["median_return"] or 0)<-EPSILON else 2.5
        breadth=clamp(s["rise_ratio"] or 0)*15+direction
        strong=clamp((s["strong_rise_ratio"] or 0)/.30)*15
        valid=s["valid_bar_count"]; limit_ratio=s["limit_up_count"]/valid if valid else 0
        linkage=0 if s["limit_up_count"]==0 else 1 if s["limit_up_count"]==1 else 3 if s["limit_up_count"]==2 else 5
        limit=clamp(limit_ratio/.10)*10+linkage
        state=activity_state(s["equal_weight_return"],r20,history_days>=20)
        activity=ACTIVITY[state]
        if r20 is not None and r20>=1.2 and (mr20 is None or mr20<1): activity=min(activity,12)
        recent=[x["equal_weight_return"] for x in history[-5:] if x["equal_weight_return"] is not None]
        if len(recent)<3: persistence=2.5
        else:
            cumulative=sum(recent); up=sum(x>EPSILON for x in recent)
            persistence=5 if cumulative>EPSILON and up>=3 else 4 if cumulative>EPSILON else 2.5 if abs(cumulative)<=EPSILON else 1.5 if up>=2 else 0
        coverage=s["coverage_ratio"]
        quality=5 if coverage>=.95 and s["data_status"]=="complete" else 4 if coverage>=.85 else 2.5 if coverage>=.70 else 1 if coverage>0 else 0
        total=round(min(100,max(0,strength+breadth+strong+limit+activity+persistence+quality)),2)
        confidence="high" if coverage>=.95 and valid>=8 and s["data_status"]=="complete" else "medium" if coverage>=.8 and valid>=5 else "low" if valid else "unavailable"
        evidence={"equal_weight_percentile":percentile(s["equal_weight_return"],ew),"median_return_percentile":percentile(s["median_return"],med),"first_limit_count":s["first_limit_count"],"broken_limit_count":s["broken_limit_count"],"history_insufficient":history_days<20,"snapshot_status":s["data_status"]}
        provisional.append(dict(snapshot=s,total=total,strength=strength,breadth=breadth,strong=strong,limit=limit,activity=activity,persistence=persistence,quality=quality,r5=r5,r20=r20,mr20=mr20,state=state,days=history_days,confidence=confidence,evidence=evidence))
    ordered=sorted(provisional,key=lambda x:(-x["total"],-(x["snapshot"]["equal_weight_return"] if x["snapshot"]["equal_weight_return"] is not None else float('-inf')),x["snapshot"]["industry_code"]))
    count=len(ordered); result=[]
    for rank,x in enumerate(ordered,1):
        s=x["snapshot"]; pct=1 if count==1 else (count-rank)/(count-1)
        vals=[round(x[k],2) for k in ("strength","breadth","strong","limit","activity","persistence","quality")]
        result.append(IndustryScore(__import__('datetime').date.fromisoformat(s["trade_date"]),s["classification"],s["classification_version"],s["industry_code"],s["industry_level"],x["total"],*vals,x["r5"],x["r20"],x["mr20"],x["state"],x["days"],rank,count,round(pct,6),x["confidence"],version,json.dumps(x["evidence"],ensure_ascii=False,sort_keys=True)))
    return result
