"""Persistent PR6.4 quality-score records and isolated scoring-run ledger."""
from __future__ import annotations
import json, sqlite3, uuid
from datetime import datetime, timezone
from decimal import Decimal
from .quality import THEORETICAL_MAX_SCORE, Component

def _now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def _json(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

def list_scoreable_events(connection, start, end, detection_version, symbols=None):
    sql="""SELECT * FROM first_limit_events WHERE trade_date BETWEEN ? AND ? AND detection_version=?
           AND detection_status='detected' AND is_first_limit=1"""; args=[str(start),str(end),detection_version]
    if symbols:
        sql += ' AND symbol IN ('+','.join('?' for _ in symbols)+')'; args.extend(symbols)
    return connection.execute(sql+' ORDER BY trade_date,symbol',args).fetchall()

def daily_inputs(connection,event):
    code=event['symbol'].split('.')[0]; day=event['trade_date']
    prior=connection.execute("""SELECT trade_date,close,volume FROM a_share_daily_bars
       WHERE stock_code=? AND adjustment='none' AND trade_date<? ORDER BY trade_date DESC LIMIT 20""",(code,day)).fetchall()
    target=connection.execute("""SELECT open,high,low,close,volume,amount FROM a_share_daily_bars
       WHERE stock_code=? AND adjustment='none' AND trade_date=?""",(code,day)).fetchone()
    return list(reversed(prior)),target

def current_industry_mapping(connection, symbol):
    code=symbol.split('.')[0]
    return connection.execute("""SELECT classification_system,sector_code,snapshot_date,lookahead_bias_warning
        FROM sector_memberships WHERE stock_code=? AND is_current=1 ORDER BY classification_system,sector_code LIMIT 1""",(code,)).fetchone()

def same_day_trend_score(connection, mapping, day):
    if mapping is None: return None
    return connection.execute("""SELECT trend_score,source FROM sector_scores
        WHERE sector_code=? AND trade_date=? ORDER BY source LIMIT 1""",(mapping['sector_code'],str(day))).fetchone()

def upsert_score(connection, event, scoring_version, summary, components):
    now=_now(); values=(event['id'],event['symbol'],event['trade_date'],event['detection_version'],scoring_version,summary['score_status'],str(summary['earned_score']),str(THEORETICAL_MAX_SCORE),str(summary['determinable_max_score']),str(summary['coverage_ratio']),int(summary['is_complete']),int(summary['is_approximate']),_json(summary['reasons']),'first_limit_quality_v1',now,now)
    connection.execute("""INSERT INTO first_limit_quality_scores(event_id,symbol,trade_date,detection_version,scoring_version,score_status,earned_score,theoretical_max_score,determinable_max_score,coverage_ratio,is_complete,is_approximate,reasons_json,rule_version,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id,scoring_version) DO UPDATE SET
      score_status=excluded.score_status,earned_score=excluded.earned_score,determinable_max_score=excluded.determinable_max_score,coverage_ratio=excluded.coverage_ratio,is_complete=excluded.is_complete,is_approximate=excluded.is_approximate,reasons_json=excluded.reasons_json,rule_version=excluded.rule_version,updated_at=excluded.updated_at""",values)
    score=connection.execute('SELECT id FROM first_limit_quality_scores WHERE event_id=? AND scoring_version=?',(event['id'],scoring_version)).fetchone()
    for component in components:
        connection.execute("""INSERT INTO first_limit_quality_components(score_id,component_key,component_status,earned_score,max_score,raw_value_json,formula_version,source_table,source_date,source_version,reasons_json,is_approximate)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(score_id,component_key) DO UPDATE SET component_status=excluded.component_status,earned_score=excluded.earned_score,max_score=excluded.max_score,raw_value_json=excluded.raw_value_json,formula_version=excluded.formula_version,source_table=excluded.source_table,source_date=excluded.source_date,source_version=excluded.source_version,reasons_json=excluded.reasons_json,is_approximate=excluded.is_approximate""",
          (score['id'],component.key,component.status.value,None if component.score is None else str(component.score),str(component.maximum),_json(component.raw),'first_limit_quality_v1',_source_table(component.key),event['trade_date'],event['detection_version'],_json(component.reasons),int(component.approximate)))
    return score['id']

def _source_table(key):
    return 'a_share_daily_bars' if key in {'pre_position','volume_expansion','amount','candle_shape'} else 'authoritative_turnover' if key=='turnover' else 'sector_memberships/sector_scores'

def create_run(connection, parameters, dry_run=False, run_id=None):
    identifier=run_id or uuid.uuid4().hex; now=_now()
    connection.execute("INSERT INTO first_limit_quality_runs(run_id,parameters_json,status,is_dry_run,started_at,created_at,updated_at) VALUES(?,?, 'running',?,?,?,?)",(identifier,_json(parameters),int(dry_run),now,now,now)); return identifier
def resumable_run(connection,run_id,parameters):
    row=connection.execute('SELECT * FROM first_limit_quality_runs WHERE run_id=?',(run_id,)).fetchone()
    if row is None: raise LookupError('score run not found: '+run_id)
    if row['parameters_json']!=_json(parameters): raise ValueError('score run parameters are incompatible with --resume')
    return row
def completed_keys(connection,run_id): return {r[0] for r in connection.execute("SELECT item_key FROM first_limit_quality_run_items WHERE run_id=? AND status='success'",(run_id,))}
def record_item(connection,run_id,key,status,score_id=None,result=None,error=None):
    connection.execute("""INSERT INTO first_limit_quality_run_items(run_id,item_key,status,score_id,result_json,last_error,updated_at) VALUES(?,?,?,?,?,?,?)
      ON CONFLICT(run_id,item_key) DO UPDATE SET status=excluded.status,score_id=excluded.score_id,result_json=excluded.result_json,last_error=excluded.last_error,updated_at=excluded.updated_at""",(run_id,key,status,score_id,_json(result) if result is not None else None,error,_now()))
def finish_run(connection,run_id,status,planned,success,skipped,failed,last_error=None):
    now=_now(); connection.execute("UPDATE first_limit_quality_runs SET status=?,planned_count=?,success_count=?,skipped_count=?,failure_count=?,last_error=?,finished_at=?,updated_at=? WHERE run_id=?",(status,planned,success,skipped,failed,last_error,now,now,run_id))
