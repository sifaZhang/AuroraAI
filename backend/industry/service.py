from __future__ import annotations
import json
from dataclasses import asdict
from datetime import date,timedelta
from backend.data_sources.industry_snapshots.service import PRICE_MATCH_TOLERANCE,_security_id,_status
from backend.strategy.first_limit.rules import resolve_limit_prices,resolve_price_limit_rule
from .models import SymbolIndustryContext,SCORE_VERSION,EffectiveIndustryContext
from .repository import IndustryScoreRepository

SORTS={"score":"total_score","return":"equal_weight_return","median_return":"median_return","rise_ratio":"rise_ratio","limit_up":"limit_up_count","first_limit":"first_limit_count","turnover":"turnover_amount","turnover_ratio_5d":"turnover_ratio_5d","turnover_ratio_20d":"turnover_ratio_20d","coverage":"coverage_ratio"}
class IndustryService:
 def __init__(self,connection):self.connection=connection;self.scores=IndustryScoreRepository(connection)
 def latest_date(self):
  r=self.connection.execute("SELECT MAX(trade_date) FROM industry_daily_scores WHERE score_version=?",(SCORE_VERSION,)).fetchone();return r[0]
 def is_score_complete(self,trade_date,score_version=SCORE_VERSION):
  counts={int(r[0]):int(r[1]) for r in self.connection.execute("SELECT industry_level,COUNT(*) FROM industry_nodes GROUP BY industry_level")}
  snap={int(r[0]):int(r[1]) for r in self.connection.execute("SELECT industry_level,COUNT(*) FROM industry_daily_snapshots WHERE trade_date=? GROUP BY industry_level",(str(trade_date),))}
  score={int(r[0]):int(r[1]) for r in self.connection.execute("SELECT industry_level,COUNT(*) FROM industry_daily_scores WHERE trade_date=? AND score_version=? GROUP BY industry_level",(str(trade_date),score_version))}
  return all(counts.get(x,0)>0 and counts.get(x)==snap.get(x)==score.get(x) for x in (1,2,3))
 def get_nearest_score_date(self,trade_date,*,direction='previous',score_version=SCORE_VERSION):
  if direction not in {'previous','next'}:raise ValueError('direction must be previous or next')
  op='<' if direction=='previous' else '>';order='DESC' if direction=='previous' else 'ASC'
  for r in self.connection.execute(f"SELECT DISTINCT trade_date FROM industry_daily_scores WHERE score_version=? AND trade_date{op}? ORDER BY trade_date {order}",(score_version,str(trade_date))):
   if self.is_score_complete(r[0],score_version):return date.fromisoformat(r[0])
  return None
 def get_latest_score_date(self,score_version=SCORE_VERSION):
  for r in self.connection.execute("SELECT DISTINCT trade_date FROM industry_daily_scores WHERE score_version=? ORDER BY trade_date DESC",(score_version,)):
   if self.is_score_complete(r[0],score_version):return date.fromisoformat(r[0])
  return None
 def get_previous_score_date(self,trade_date,score_version=SCORE_VERSION):return self.get_nearest_score_date(trade_date,direction='previous',score_version=score_version)
 def get_next_score_date(self,trade_date,score_version=SCORE_VERSION):return self.get_nearest_score_date(trade_date,direction='next',score_version=score_version)
 def get_effective_industry_context(self,symbol,trade_date,*,context_type='official_close',score_version=SCORE_VERSION):
  m=self.get_symbol_membership(symbol)
  if not m:return EffectiveIndustryContext(None,None,None,None,None,None,None,None,'unavailable')
  for level in (3,2,1):
   code=m[f'level{level}_code']; score=self.get_industry_score(trade_date,code,score_version); snap=self.get_industry_snapshot(trade_date,code)
   if score and snap and (level==1 or (score.confidence in {'high','medium'} and snap.get('valid_bar_count',0)>=8 and snap.get('coverage_ratio',0)>=.8)):
    return EffectiveIndustryContext(level,code,m[f'level{level}_name'],score.total_score,score.rank_in_level,score.industry_count_in_level,score.confidence,None if level==3 else f'level{level+1}_insufficient','complete')
  return EffectiveIndustryContext(None,None,None,None,None,None,None,'all_levels_insufficient','unavailable')
 def get_symbol_membership(self,symbol):
  r=self.connection.execute("SELECT * FROM industry_memberships_current WHERE symbol=?",(symbol.upper(),)).fetchone();return dict(r) if r else None
 def get_industry_snapshot(self,trade_date,code):
  r=self.connection.execute("SELECT * FROM industry_daily_snapshots WHERE trade_date=? AND industry_code=?",(str(trade_date),code)).fetchone();return dict(r) if r else None
 def get_industry_score(self,trade_date,code,version=SCORE_VERSION):return self.scores.get_score(trade_date,code,version)
 def tree(self):
  rows=[dict(r) for r in self.connection.execute("SELECT * FROM industry_nodes ORDER BY industry_level,industry_code")];by={r['industry_code']:{**r,'children':[]} for r in rows}
  roots=[]
  for r in rows:(by[r['parent_code']]['children'] if r['parent_code'] else roots).append(by[r['industry_code']])
  return roots
 def list_industries(self,*,trade_date=None,level=2,parent_code=None,search="",sort_by="score",order="desc",page=1,page_size=50):
  if sort_by not in SORTS or order not in {'asc','desc'} or level not in (1,2,3) or page<1 or not 1<=page_size<=200:raise ValueError("invalid industry list parameters")
  day=str(trade_date or self.latest_date() or '')
  clauses=["n.industry_level=?"];p=[level]
  if parent_code:clauses.append("n.parent_code=?");p.append(parent_code)
  if search:clauses.append("n.industry_name LIKE ?");p.append(f"%{search}%")
  where=' AND '.join(clauses);field=SORTS[sort_by]
  total=self.connection.execute(f"SELECT COUNT(*) FROM industry_nodes n WHERE {where}",p).fetchone()[0]
  rows=self.connection.execute(f"""SELECT n.industry_code,n.industry_name,n.parent_code,parent.industry_name AS parent_name,s.*,d.constituent_count,d.valid_bar_count,d.coverage_ratio,d.equal_weight_return,d.median_return,d.rise_ratio,d.strong_rise_ratio,d.limit_up_count,d.first_limit_count,d.turnover_amount,d.data_status
   FROM industry_nodes n LEFT JOIN industry_nodes parent ON parent.classification=n.classification AND parent.classification_version=n.classification_version AND parent.industry_code=n.parent_code LEFT JOIN industry_daily_snapshots d ON d.industry_code=n.industry_code AND d.trade_date=? LEFT JOIN industry_daily_scores s ON s.industry_code=n.industry_code AND s.trade_date=? AND s.score_version=? WHERE {where}
   ORDER BY ({field} IS NULL),{field} {order.upper()},n.industry_code LIMIT ? OFFSET ?""",[day,day,SCORE_VERSION,*p,page_size,(page-1)*page_size]).fetchall()
  return {'trade_date':day or None,'level':level,'page':page,'page_size':page_size,'total':total,'items':[dict(r) for r in rows]}
 def list_constituents(self,code,level,limit=200,trade_date=None):
  if level not in (1,2,3):raise ValueError("invalid level")
  target=date.fromisoformat(str(trade_date or self.latest_date() or ""))
  day=target.isoformat()
  rows=self.connection.execute(f"""WITH members AS (
    SELECT symbol,level1_code,level1_name,level2_code,level2_name,level3_code,level3_name
    FROM industry_memberships_current WHERE level{level}_code=? ORDER BY symbol LIMIT ?
   ), latest_status AS (
    SELECT * FROM (SELECT s.*,ROW_NUMBER() OVER(PARTITION BY s.symbol ORDER BY s.effective_date DESC) rn
      FROM a_share_security_status_history s JOIN members m ON m.symbol=s.symbol WHERE s.effective_date<=?) WHERE rn=1
   ), previous_bars AS (
    SELECT b.stock_code,b.close FROM a_share_daily_bars b JOIN (
      SELECT b2.stock_code,MAX(b2.trade_date) trade_date FROM a_share_daily_bars b2
      JOIN a_share_security_master master ON master.stock_code=b2.stock_code
      JOIN members m ON m.symbol=master.symbol
      WHERE b2.trade_date<? AND b2.adjustment='none' GROUP BY b2.stock_code
    ) p ON p.stock_code=b.stock_code AND p.trade_date=b.trade_date WHERE b.adjustment='none'
   ), latest_detection_run AS (
    SELECT run_id FROM first_limit_sync_runs
    WHERE sync_type='detect' AND status IN ('success','partial')
    ORDER BY finished_at DESC,updated_at DESC LIMIT 1
   ), latest_detection AS (
    SELECT i.item_key,i.result_json FROM first_limit_sync_items i
    JOIN latest_detection_run r ON r.run_id=i.run_id
    JOIN members m ON i.item_key=m.symbol || ':' || ?
    WHERE i.status='success'
   )
   SELECT m.*,master.*,status.effective_date,status.board_type AS status_board_type,status.is_st,status.is_suspended,status.no_price_limit,
     status.listed_date AS status_listed_date,status.delisted_date AS status_delisted_date,status.source AS status_source,
     bar.close,meta.pre_close,meta.source_upper_limit,meta.source_lower_limit,previous_bars.close AS previous_close,
     latest_detection.result_json
   FROM members m
   LEFT JOIN a_share_security_master master ON master.symbol=m.symbol
   LEFT JOIN latest_status status ON status.symbol=m.symbol
   LEFT JOIN a_share_daily_bars bar ON bar.stock_code=master.stock_code AND bar.trade_date=? AND bar.adjustment='none'
   LEFT JOIN first_limit_daily_metadata meta ON meta.symbol=m.symbol AND meta.trade_date=?
   LEFT JOIN previous_bars ON previous_bars.stock_code=master.stock_code
   LEFT JOIN latest_detection ON latest_detection.item_key=m.symbol || ':' || ?
   ORDER BY m.symbol""",(code,limit,day,day,day,day,day,day)).fetchall()
  return [self._constituent_market_status(row,target) for row in rows]
 def _constituent_market_status(self,row,target):
  item=dict(row);symbol=item['symbol']
  status_row={**item,'board_type':item.get('status_board_type'),'source':item.get('status_source'),'listed_date':item.get('status_listed_date'),'delisted_date':item.get('status_delisted_date')}
  status=_status(status_row,symbol) if item.get('effective_date') else None
  rule=resolve_price_limit_rule(_security_id(symbol),target,status)
  pre_close=item.get('pre_close') if item.get('pre_close') is not None else item.get('previous_close')
  prices=resolve_limit_prices(pre_close,rule,source_upper_limit=item.get('source_upper_limit'),source_lower_limit=item.get('source_lower_limit'))
  close=item.get('close')
  item['limit_up_price']=float(prices.upper_limit) if prices.upper_limit is not None else None
  item['is_close_limit_up']=None if not prices.reliable or prices.upper_limit is None or close is None else abs(float(close)-float(prices.upper_limit))<=PRICE_MATCH_TOLERANCE
  item['close_limit_status']='unavailable' if item['is_close_limit_up'] is None else ('limit_up' if item['is_close_limit_up'] else 'not_limit_up')
  payload=json.loads(item['result_json']) if item.get('result_json') else None
  detected=payload and payload.get('detection_status') in {'detected','not_first_limit','excluded'} and payload.get('is_first_limit') is not None
  item['first_limit_status']='first_limit' if detected and payload['is_first_limit'] else ('not_first_limit' if detected else 'not_detected')
  item.pop('result_json',None)
  return item
 def get_industry_history(self,code,start,end):
  rows=self.connection.execute("""SELECT d.*,s.total_score,s.turnover_ratio_20d,s.price_volume_state,s.confidence FROM industry_daily_snapshots d LEFT JOIN industry_daily_scores s ON s.trade_date=d.trade_date AND s.industry_code=d.industry_code AND s.score_version=? WHERE d.industry_code=? AND d.trade_date BETWEEN ? AND ? ORDER BY d.trade_date""",(SCORE_VERSION,code,str(start),str(end))).fetchall();return [dict(r) for r in rows]
 def detail(self,code,trade_date=None):
  day=str(trade_date or self.latest_date() or '');node=self.connection.execute("SELECT * FROM industry_nodes WHERE industry_code=?",(code,)).fetchone()
  if not node:return None
  snapshot=self.get_industry_snapshot(day,code);score=self.get_industry_score(day,code)
  parent=dict(self.connection.execute("SELECT * FROM industry_nodes WHERE industry_code=?",(node['parent_code'],)).fetchone()) if node['parent_code'] else None
  children=[dict(r) for r in self.connection.execute("SELECT * FROM industry_nodes WHERE parent_code=? ORDER BY industry_code",(code,))]
  return {'node':dict(node),'parent':parent,'children':children,'snapshot':snapshot,'score':asdict(score) if score else None}
 def get_symbol_industry_context(self,symbol,trade_date,score_version=SCORE_VERSION):
  m=self.get_symbol_membership(symbol)
  if not m:return SymbolIndustryContext(symbol.upper(),trade_date,*([None]*23),"membership_missing")
  values=[];snapshots={}
  missing_snapshot=False;missing_score=False
  for level in (1,2,3):
   code=m[f'level{level}_code'];score=self.get_industry_score(trade_date,code,score_version);snap=self.get_industry_snapshot(trade_date,code)
   missing_snapshot|=snap is None;missing_score|=score is None
   values.extend([code,m[f'level{level}_name'],score.total_score if score else None,score.rank_in_level if score else None,score.industry_count_in_level if score else None,score.confidence if score else None,score.price_volume_state if score else None]);snapshots[level]=snap
  status='snapshot_missing' if missing_snapshot else 'score_missing' if missing_score else 'complete'
  return SymbolIndustryContext(symbol.upper(),trade_date,*values,snapshots[2],snapshots[3],status)
