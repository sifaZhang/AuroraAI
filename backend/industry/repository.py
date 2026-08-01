from __future__ import annotations
import sqlite3
from dataclasses import asdict, fields
from datetime import date,datetime,timezone
from .models import IndustryScore

FIELDS=tuple(f.name for f in fields(IndustryScore))
def _vals(x): return tuple(v.isoformat() if isinstance(v,date) else v for v in (getattr(x,n) for n in FIELDS))
class IndustryScoreRepository:
 def __init__(self,connection): self.connection=connection
 def get_score(self,trade_date,industry_code,score_version="industry_score_v1"):
  r=self.connection.execute("SELECT * FROM industry_daily_scores WHERE trade_date=? AND industry_code=? AND score_version=?",(str(trade_date),industry_code,score_version)).fetchone(); return self._model(r) if r else None
 def list_scores(self,trade_date,level=None,score_version="industry_score_v1"):
  where="trade_date=? AND score_version=?"; p=[str(trade_date),score_version]
  if level: where+=" AND industry_level=?"; p.append(level)
  return [self._model(r) for r in self.connection.execute(f"SELECT * FROM industry_daily_scores WHERE {where} ORDER BY industry_level,rank_in_level",p)]
 def list_score_history(self,industry_code,start_date,end_date,score_version="industry_score_v1"):
  return [self._model(r) for r in self.connection.execute("SELECT * FROM industry_daily_scores WHERE industry_code=? AND trade_date BETWEEN ? AND ? AND score_version=? ORDER BY trade_date",(industry_code,str(start_date),str(end_date),score_version))]
 def replace_scores_for_date(self,scores,force=False):
  changed=[x for x in scores if force or self.get_score(x.trade_date,x.industry_code,x.score_version)!=x]
  if not changed:return 0
  now=datetime.now(timezone.utc).isoformat(timespec="seconds"); cols=FIELDS+("updated_at",); ph=','.join('?' for _ in cols); updates=','.join(f"{n}=excluded.{n}" for n in FIELDS if n not in {'trade_date','classification','classification_version','industry_code','score_version'})+",updated_at=excluded.updated_at"
  with self.connection:self.connection.executemany(f"INSERT INTO industry_daily_scores({','.join(cols)}) VALUES({ph}) ON CONFLICT(trade_date,classification,classification_version,industry_code,score_version) DO UPDATE SET {updates}",[_vals(x)+(now,) for x in changed])
  return len(changed)
 @staticmethod
 def _model(r):
  d={n:r[n] for n in FIELDS};d['trade_date']=date.fromisoformat(d['trade_date']);return IndustryScore(**d)

def snapshot_rows(connection,trade_date,level=None):
 where="trade_date=?";p=[str(trade_date)]
 if level:where+=" AND industry_level=?";p.append(level)
 return [dict(r) for r in connection.execute(f"SELECT * FROM industry_daily_snapshots WHERE {where} ORDER BY industry_level,industry_code",p)]
def histories(connection,codes,trade_date,limit=20):
 return {c:[dict(r) for r in connection.execute("SELECT * FROM industry_daily_snapshots WHERE industry_code=? AND trade_date<? ORDER BY trade_date DESC LIMIT ?",(c,str(trade_date),limit)).fetchall()[::-1]] for c in codes}
