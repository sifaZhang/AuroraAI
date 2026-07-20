"use strict";
const assert=require("assert");
const {createDashboard,scoreClass}=require("../../frontend/market-pulse.js");

class Element{
  constructor(){this.innerHTML="";this.textContent="";this.hidden=false;this.disabled=false;this.listeners={}}
  addEventListener(name,handler){this.listeners[name]=handler}
}
function documentMock(){const ids=["sector-rows","source-statuses","refresh-sectors","pulse-error","last-trade-date","last-refresh","refresh-state","sector-count"],elements={};ids.forEach(id=>elements[id]=new Element());return{elements,getElementById:id=>elements[id]}}
function response(data,ok=true,status=200){return{ok,status,json:async()=>data}}
function sectors(count=31){return{trade_date:"2026-07-20",latest_trade_date:"2026-07-20",items:Array.from({length:count},(_,index)=>({source:"sw_l1",sector_name:`行业${index+1}`,trend_score:index?60:70,trend_level:"strong",updated_at:"2026-07-20T10:00:00+00:00"}))}}
function health(){return{items:[{source:"sw_l1",display_name:"申万一级行业",status:"healthy"},{source:"sw_l2",display_name:"申万二级行业",status:"unavailable"},{source:"eastmoney",display_name:"东方财富行业",status:"unavailable"}]}}

async function testInitialLoad(){
  const doc=documentMock(),calls=[];
  const fetch=async url=>{calls.push(url);return response(url.includes("data-source-health")?health():sectors())};
  const dashboard=createDashboard({document:doc,fetch,setTimeout:()=>{}});await dashboard.init();
  assert.deepStrictEqual(calls,["/api/market-pulse/sectors","/api/data-source-health"]);
  assert.strictEqual((doc.elements["sector-rows"].innerHTML.match(/<tr>/g)||[]).length,31);
  assert.strictEqual((doc.elements["source-statuses"].innerHTML.match(/source-status-card/g)||[]).length,3);
  assert.strictEqual(scoreClass(70),"score-70");
}
async function testRefreshAndReload(){
  const doc=documentMock(),calls=[];let disabledDuringPost=false;
  const fetch=async(url,init={})=>{calls.push([url,init.method||"GET"]);if(url==="/api/market-pulse/refresh"){disabledDuringPost=doc.elements["refresh-sectors"].disabled;return response({job_id:7,status:"queued"})}if(url.endsWith("/7"))return response({job_id:7,status:"completed",progress:100});return response(url.includes("data-source-health")?health():sectors())};
  const dashboard=createDashboard({document:doc,fetch,setTimeout:()=>{}});await dashboard.refresh();
  assert.strictEqual(disabledDuringPost,true);assert.strictEqual(doc.elements["refresh-sectors"].disabled,false);
  assert(calls.some(call=>call[0]==="/api/market-pulse/refresh"&&call[1]==="POST"));
  assert(calls.some(call=>call[0]==="/api/market-pulse/sectors"));
  assert(calls.some(call=>call[0]==="/api/data-source-health"));
}
async function testEmptyAndError(){
  const doc=documentMock();const dashboard=createDashboard({document:doc,fetch:async()=>response({items:[]}),setTimeout:()=>{}});
  dashboard.renderSectors({items:[]});assert(doc.elements["sector-rows"].innerHTML.includes("暂无行业数据"));
  const failing=createDashboard({document:doc,fetch:async()=>response({detail:"upstream"},false,500),setTimeout:()=>{}});
  await assert.rejects(()=>failing.load());assert.strictEqual(doc.elements["pulse-error"].hidden,false);assert(doc.elements["pulse-error"].textContent.includes("加载失败"));
}
(async()=>{await testInitialLoad();await testRefreshAndReload();await testEmptyAndError();console.log("Market Pulse frontend mock tests passed")})().catch(error=>{console.error(error);process.exit(1)});
