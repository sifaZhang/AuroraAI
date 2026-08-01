from datetime import date,timedelta
import sqlite3
from dataclasses import asdict
from fastapi import APIRouter,HTTPException,Query
from backend.expectation_gap.database import connect
from backend.industry.service import IndustryService

router=APIRouter(prefix="/api/industry",tags=["industry"])
def run(callback):
 c=connect()
 try:return callback(IndustryService(c))
 except ValueError as e:raise HTTPException(422,str(e)) from e
 except sqlite3.Error as e:raise HTTPException(500,"industry database query failed") from e
 finally:c.close()
@router.get("/tree")
def tree():return run(lambda s:{"items":s.tree()})
@router.get("/list")
def listing(trade_date:date|None=None,level:int=Query(2,ge=1,le=3),parent_code:str|None=None,search:str="",sort_by:str="score",order:str="desc",page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=200)):
 return run(lambda s:s.list_industries(trade_date=trade_date,level=level,parent_code=parent_code,search=search,sort_by=sort_by,order=order,page=page,page_size=page_size))
@router.get("/detail")
def detail(industry_code:str,trade_date:date|None=None):
 item=run(lambda s:s.detail(industry_code,trade_date))
 if item is None:raise HTTPException(404,"industry not found")
 return item
@router.get("/history")
def history(industry_code:str,start_date:date,end_date:date):return run(lambda s:{"items":s.get_industry_history(industry_code,start_date,end_date)})
@router.get("/context")
def context(symbol:str,trade_date:date):return run(lambda s:asdict(s.get_symbol_industry_context(symbol,trade_date)))
@router.get("/constituents")
def constituents(industry_code:str,level:int=Query(...,ge=1,le=3),limit:int=Query(200,ge=1,le=1000)):return run(lambda s:{"items":s.list_constituents(industry_code,level,limit)})
