"""SQLite persistence for PR6.6 context scoring; no data collection occurs here."""
from __future__ import annotations
import json,uuid
from datetime import datetime,timezone

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def dump(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,default=str)
def observations(con,start,end,detection_version,scoring_version,pullback_version,symbols=None):
 sql="""SELECT o.*,e.id event_id,q.earned_score first_limit_score FROM first_limit_pullback_observations o
 JOIN first_limit_events e ON e.id=o.event_id JOIN first_limit_quality_scores q ON q.event_id=e.id AND q.scoring_version=o.scoring_version
 WHERE o.observation_date BETWEEN ? AND ? AND o.detection_version=? AND o.scoring_version=? AND o.pullback_version=?"""
 args=[str(start),str(end),detection_version,scoring_version,pullback_version]
 if symbols: sql+=' AND o.symbol IN ('+','.join('?' for _ in symbols)+')';args+=symbols
 return con.execute(sql+' ORDER BY o.observation_date,o.symbol',args).fetchall()
def closes(con,symbol,day):
 return con.execute("SELECT close FROM a_share_daily_bars WHERE stock_code=? AND adjustment='none' AND trade_date<=? ORDER BY trade_date DESC LIMIT 20",(symbol.split('.')[0],str(day))).fetchall()[::-1]
def create_run(con,params):
 rid=uuid.uuid4().hex;t=now();con.execute("INSERT INTO first_limit_context_runs(run_id,parameters_json,status,is_dry_run,started_at,created_at,updated_at) VALUES(?,?,'running',0,?,?,?)",(rid,dump(params),t,t,t));return rid
def resume_run(con,rid,params):
 row=con.execute('SELECT * FROM first_limit_context_runs WHERE run_id=?',(rid,)).fetchone()
 if row is None:raise LookupError('context run not found: '+rid)
 if row['parameters_json']!=dump(params):raise ValueError('context run parameters are incompatible with --resume')
 return row
def done(con,rid):return {r[0] for r in con.execute("SELECT item_key FROM first_limit_context_run_items WHERE run_id=? AND status='success'",(rid,))}
def save(con,row,version,summary,components):
 t=now(); vals=(row['event_id'],row['id'],row['symbol'],row['first_limit_date'],row['observation_date'],row['detection_version'],row['scoring_version'],row['pullback_version'],version,summary['score_status'],row['first_limit_score'],row['earned_score'],None,None,None,None if summary['daily_base_score'] is None else str(summary['daily_base_score']),str(summary['daily_base_determinable_max_score']),str(summary['daily_base_coverage_ratio']),int(summary['is_complete']),int(summary['is_approximate']),summary['minute_confirm_status'],summary['final_candidate_level'],dump(summary['reasons']),t,t)
 con.execute("""INSERT INTO first_limit_context_scores(event_id,observation_id,symbol,first_limit_date,observation_date,detection_version,scoring_version,pullback_version,context_scoring_version,score_status,first_limit_score,pullback_score,industry_score,market_score,stock_trend_score,daily_base_score,daily_base_determinable_max_score,daily_base_coverage_ratio,is_complete,is_approximate,minute_confirm_status,final_candidate_level,reasons_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(observation_id,context_scoring_version) DO UPDATE SET score_status=excluded.score_status,first_limit_score=excluded.first_limit_score,pullback_score=excluded.pullback_score,daily_base_score=excluded.daily_base_score,daily_base_determinable_max_score=excluded.daily_base_determinable_max_score,daily_base_coverage_ratio=excluded.daily_base_coverage_ratio,is_complete=excluded.is_complete,is_approximate=excluded.is_approximate,reasons_json=excluded.reasons_json,updated_at=excluded.updated_at""",vals)
 sid=con.execute('SELECT id FROM first_limit_context_scores WHERE observation_id=? AND context_scoring_version=?',(row['id'],version)).fetchone()[0]
 for c in components:
  con.execute("""INSERT INTO first_limit_context_components(score_id,component_key,component_status,earned_score,max_score,raw_value_json,reasons_json,source_table,source_date,source_version,is_approximate) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(score_id,component_key) DO UPDATE SET component_status=excluded.component_status,earned_score=excluded.earned_score,raw_value_json=excluded.raw_value_json,reasons_json=excluded.reasons_json,is_approximate=excluded.is_approximate""",(sid,c.key,c.status,None if c.score is None else str(c.score),str(c.maximum),dump(c.raw),dump(c.reasons),'a_share_daily_bars',row['observation_date'],version,int(c.approximate)))
 return sid
def item(con,rid,key,status,sid=None,result=None,error=None):
 con.execute("INSERT INTO first_limit_context_run_items(run_id,item_key,status,score_id,result_json,last_error,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,item_key) DO UPDATE SET status=excluded.status,score_id=excluded.score_id,result_json=excluded.result_json,last_error=excluded.last_error,updated_at=excluded.updated_at",(rid,key,status,sid,dump(result) if result else None,error,now()))
def finish(con,rid,status,planned,success,skipped,failed,indeterminate):
 t=now();con.execute("UPDATE first_limit_context_runs SET status=?,planned_count=?,success_count=?,skipped_count=?,failure_count=?,indeterminate_count=?,finished_at=?,updated_at=? WHERE run_id=?",(status,planned,success,skipped,failed,indeterminate,t,t,rid))
