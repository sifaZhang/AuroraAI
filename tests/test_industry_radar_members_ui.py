import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "industry-radar.js"


def test_third_level_member_toggle_and_detail_actions_are_separate():
    program = f'''\
const assert = require('assert');
const {{ createRadar }} = require({SCRIPT.as_posix()!r});
;(async()=>{{
function El() {{ this.children=[]; this.style={{}}; this.dataset={{}}; this.classList={{add(){{}},remove(){{}}}}; this.firstElementChild={{style:{{}}}}; this.open=false; this.shown=0; }}
Object.defineProperty(El.prototype, 'textContent', {{get(){{return this._text||''}},set(value){{this._text=value;this.children=[]}}}});
El.prototype.appendChild=function(child){{this.children.push(child);this.firstChild=this.children[0];return child}};
El.prototype.insertBefore=function(child,before){{const i=this.children.indexOf(before);this.children.splice(i<0?this.children.length:i,0,child);this.firstChild=this.children[0];}};
El.prototype.showModal=function(){{this.open=true;this.shown+=1}}; El.prototype.close=function(){{this.open=false}};
const ids={{}}; for (const id of ['sector-rows','radar-error','industry-scroll-top','industry-table-wrap','industry-parent-header','industry-refresh-status','industry-refresh-button','industry-search','industry-sort','industry-order','detail-close','industry-detail','detail-content','industry-trade-date','constituents-content']) ids[id]=new El();
ids['industry-trade-date']._text='2026-08-07';
const l1=new El(),l2=new El(),l3=new El();l1.dataset.level='1';l2.dataset.level='2';l3.dataset.level='3';
const doc={{getElementById:id=>ids[id],querySelector:s=>s==='.industry-table'?{{scrollWidth:1240}}:null,querySelectorAll:s=>s==='[data-level]'?[l1,l2,l3]:[],createElement:()=>new El()}};
const calls=[]; const detail={{node:{{industry_name:'稀土'}},score:{{total_score:95.67,rank_in_level:3,industry_count_in_level:336}},snapshot:{{coverage_ratio:1}},parent:null,children:[]}};
const radar=createRadar({{document:doc,fetch:async url=>{{calls.push(url);if(url.includes('850999'))return {{ok:false,json:async()=>({{detail:'member API failed'}})}};if(url.startsWith('/api/industry/constituents'))return {{ok:true,json:async()=>({{items:[{{security_name:'样本股',symbol:'000506.SZ',close:18.13,close_limit_status:'limit_up',first_limit_status:'not_detected'}}]}})}};return {{ok:true,json:async()=>detail}}}}}});
radar.state.level=3;radar.state.items=[{{industry_code:'850531',industry_name:'稀土',parent_code:'801050',parent_name:'有色金属',total_score:95.67,rank_in_level:3}}];radar.render();
assert.strictEqual(ids['industry-parent-header'].textContent,'父行业');assert.strictEqual(ids['sector-rows'].children[0].children[1].textContent,'801050 有色金属');
let row=ids['sector-rows'].children[0]; row.children[0].children[0].onclick({{stopPropagation(){{}}}}); await new Promise(resolve=>setImmediate(resolve));
assert(calls[0].startsWith('/api/industry/constituents?'));assert.strictEqual(radar.state.expandedCode,'850531');assert.strictEqual(ids['industry-detail'].shown,0);assert.strictEqual(ids['sector-rows'].children.length,2);
row=ids['sector-rows'].children[0];row.children.find(cell=>cell.children[0]&&cell.children[0].className==='industry-score-detail').children[0].onclick({{stopPropagation(){{}}}});await new Promise(resolve=>setImmediate(resolve));assert.strictEqual(ids['industry-detail'].shown,1);
row.children[0].children[0].onclick({{stopPropagation(){{}}}});assert.strictEqual(radar.state.expandedCode,null);assert.strictEqual(ids['sector-rows'].children.length,1);
radar.state.level=1;radar.render();assert.strictEqual(ids['industry-parent-header'].textContent,'行业代码');assert.strictEqual(ids['sector-rows'].children[0].children[1].textContent,'850531');assert.strictEqual(ids['sector-rows'].children[0].children[2].textContent,'95.67/3');ids['sector-rows'].children[0].onclick();await new Promise(resolve=>setImmediate(resolve));assert.strictEqual(ids['industry-detail'].shown,2);
radar.state.expandedCode='850531';l1.onclick();assert.strictEqual(radar.state.expandedCode,null);
radar.state.level=3;await radar.toggleMembers({{industry_code:'850999'}});assert.strictEqual(radar.state.expandedCode,null);assert.strictEqual(ids['radar-error'].hidden,false);assert(ids['radar-error'].textContent.includes('member API failed'));
const pending=[];const radar2=createRadar({{document:doc,fetch:url=>new Promise(resolve=>pending.push({{url,resolve}}))}});radar2.state.level=3;const oldLoad=radar2.load();assert.strictEqual(ids['sector-rows'].children[0].children[0].textContent,'加载中…');radar2.state.level=2;const newLoad=radar2.load();pending[1].resolve({{ok:true,json:async()=>({{trade_date:'2026-08-07',total:1,items:[{{industry_code:'801054',industry_name:'二级'}}]}})}});await newLoad;pending[0].resolve({{ok:true,json:async()=>({{trade_date:'2026-08-07',total:1,items:[{{industry_code:'850001',industry_name:'旧三级'}}]}})}});await oldLoad;assert.strictEqual(radar2.state.level,2);assert.strictEqual(radar2.state.items[0].industry_code,'801054');
const radar3=createRadar({{document:doc,fetch:async url=>{{const page=Number(new URL(url,'http://x').searchParams.get('page'));return {{ok:true,json:async()=>({{trade_date:'2026-08-07',total:336,items:Array.from({{length:page===1?200:136}},(_,index)=>({{industry_code:`850${{page}}${{index}}`,industry_name:'三级'}}))}})}}}}}});radar3.state.level=3;await radar3.load();assert.strictEqual(radar3.state.total,336);assert.strictEqual(radar3.state.items.length,336);
}})().catch(error=>{{console.error(error);process.exit(1)}});
'''
    subprocess.run(["node", "-e", program], check=True, text=True, encoding="utf-8")
