from dataclasses import dataclass,asdict
from datetime import date
from backend.industry.service import IndustryService

@dataclass(frozen=True)
class FirstLimitIndustryContext:
 symbol:str; trade_date:date; first_limit_score_date:date|None; previous_score_date:date|None
 membership:dict|None; effective:object; first_limit_score:float|None; first_limit_rank:int|None; previous_score:float|None; previous_rank:int|None; status:str; warnings:tuple[str,...]=()
 def evidence(self):
  value=asdict(self);value['effective']=asdict(self.effective) if self.effective else None;return value

def build_first_limit_industry_context(connection,symbol,first_limit_date,trade_date):
 service=IndustryService(connection); membership=service.get_symbol_membership(symbol)
 if not membership:return FirstLimitIndustryContext(symbol,trade_date,None,None,None,None,None,None,None,None,'membership_missing')
 first=service.get_effective_industry_context(symbol,first_limit_date); previous=service.get_previous_score_date(trade_date)
 prior=service.get_effective_industry_context(symbol,previous) if previous else None
 effective=service.get_effective_industry_context(symbol,first_limit_date)
 status='complete' if effective.status=='complete' and prior and prior.status=='complete' else 'previous_score_missing' if not previous else effective.status
 return FirstLimitIndustryContext(symbol,trade_date,first_limit_date,previous,membership,effective,effective.effective_score,effective.effective_rank,prior.effective_score if prior else None,prior.effective_rank if prior else None,status)
