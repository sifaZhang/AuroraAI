from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from .fundamental_analyst import AnalystInputError,build_context,context_mode
from .json_store import read_json,write_json_atomic
from .llm.base import LlmProviderError,StructuredProvider
from .llm.factory import create_provider
from .profile_builder import DEFAULT_RESEARCH_ROOT
from .valuation_schema import validate_valuation
PROMPT_VERSION='valuation-v1';PROMPT_PATH=Path(__file__).parent/'prompts'/'valuation_agent.md'
class ValuationAgent:
 def __init__(self,*,provider:StructuredProvider|None=None,research_root:Path=DEFAULT_RESEARCH_ROOT):self.provider,self.research_root=provider,research_root
 def run(self,symbol:str,*,version:str|None=None,force:bool=False,dry_run:bool=False):
  folder,v=self._folder(symbol,version);out=folder/'valuation.json'
  if out.exists() and not force and not dry_run:raise FileExistsError(f'valuation.json already exists for {v}; use --force')
  company,snapshot,evidence,analyst,skeptic,vi=(read_json(x) for x in (folder.parents[1]/'company.json',folder/'input_snapshot.json',folder/'evidence.json',folder/'analyst.json',folder/'skeptic.json',folder/'valuation_input.json'))
  if not all((company,analyst,skeptic,vi)) or vi.get('symbol')!=symbol.upper():raise AnalystInputError('valuation inputs missing or mismatched')
  context,ids=build_context(folder,snapshot,evidence,'valuation');claims={x.get('claim_id') for x in analyst.get('claims',[])}
  prompt=PROMPT_PATH.read_text(encoding='utf-8');request=self._request(company,vi,analyst,skeptic,v,context)
  if dry_run:return {'symbol':symbol.upper(),'version':v,'context_mode':context_mode(context),'context_characters':len(context),'evidence_ids':sorted(ids),'dry_run':True}
  p=self.provider or create_provider(role='valuation'); raw=self._generate(p,prompt,request)
  try:d=self._validate(json.loads(raw.content),raw,symbol.upper(),v,ids)
  except Exception as first:
   repair=p.generate_structured(system_prompt=prompt,user_prompt=request+f'\nCorrect validation errors and return complete JSON only:\n{str(first)[:1200]}')
   try:d=self._validate(json.loads(repair.content),repair,symbol.upper(),v,ids)
   except Exception as e:raise AnalystInputError(f'LLM output failed schema/semantic validation: {str(e).splitlines()[0]}') from first
  d.generation.update(context_mode=context_mode(context),context_characters=len(context));write_json_atomic(out,d.model_dump(mode='json'));return {'symbol':symbol.upper(),'version':v,'path':str(out),'generation':d.generation}
 def _folder(self,symbol,version):
  a=self.research_root/symbol.upper()/'analysis';choices=[x for x in a.iterdir() if x.is_dir() and (x/'valuation_input.json').exists() and (x/'analyst.json').exists() and (x/'skeptic.json').exists() and not (x/'valuation.json').exists()];f=a/version if version else sorted(choices,key=lambda x:x.name)[-1]
  if not f.exists():raise AnalystInputError('no complete valuation version available')
  return f,f.name
 @staticmethod
 def _generate(p,prompt,request):
  try:r=p.generate_structured(system_prompt=prompt,user_prompt=request);json.loads(r.content);return r
  except (LlmProviderError,json.JSONDecodeError) as e:
   try:return p.generate_structured(system_prompt=prompt,user_prompt=request+'\nReturn complete valid JSON only.')
   except LlmProviderError:raise e
 @staticmethod
 def _validate(x,r,s,v,ids):
  if not isinstance(x,dict):raise AnalystInputError('LLM JSON output must be object')
  x['generation']={'provider':r.provider,'model':r.model,'prompt_version':PROMPT_VERSION,'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'input_tokens':r.input_tokens,'output_tokens':r.output_tokens,'latency_ms':r.latency_ms}
  return validate_valuation(x,symbol=s,version=v,evidence_ids=ids)
 @staticmethod
 def _request(c,vi,a,sk,v,ctx):
  skeleton={'schema_version':1,'symbol':vi['symbol'],'analysis_version':v,'normalized_earnings':{'period':'FY2026E','unit':'CNY_100M','bear':None,'base':None,'bull':None,'method':'insufficient_evidence','key_assumptions':[],'assumption_metadata':[],'evidence_ids':[],'forecast_quality':'INSUFFICIENT','confidence':None},'valuation_method':{'primary':'insufficient_evidence','secondary':[],'reasoning':'','multiple_source':'assumption'},'multiples':{'bear':None,'base':None,'bull':None,'metric':'PE','assumption_rationale':[]},'fair_value_per_share':{'bear':None,'base':None,'bull':None},'reverse_valuation':{'current_price':None,'current_market_cap':None,'implied_normalized_earnings':None,'implied_multiple':None,'scenarios':[],'interpretation':''},'dividend_cross_check':{'current_yield':None,'sustainable_dividend':None,'sustainable_yield':None,'assessment':''},'limitations':[],'confidence':0.0}
  return 'Return valuation JSON exactly matching this shape (generation added locally):\n'+json.dumps(skeleton,ensure_ascii=False)+'\nCOMPANY:\n'+json.dumps(c,ensure_ascii=False)+'\nVALUATION INPUT:\n'+json.dumps(vi,ensure_ascii=False)+'\nANALYST:\n'+json.dumps(a,ensure_ascii=False)+'\nSKEPTIC:\n'+json.dumps(sk,ensure_ascii=False)+'\nSOURCE EVIDENCE:\n'+ctx
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--symbol',required=True);p.add_argument('--version');p.add_argument('--force',action='store_true');p.add_argument('--dry-run',action='store_true');p.add_argument('--research-root',type=Path,default=DEFAULT_RESEARCH_ROOT);a=p.parse_args(argv)
 try:print(ValuationAgent(research_root=a.research_root).run(a.symbol,version=a.version,force=a.force,dry_run=a.dry_run));return 0
 except (AnalystInputError,LlmProviderError,FileExistsError,ValueError) as e:print({'status':'failed','error':str(e)});return 1
if __name__=='__main__':raise SystemExit(main())
