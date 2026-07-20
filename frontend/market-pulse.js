(function(root){
  "use strict";
  const terminal=new Set(["completed","partial","failed"]);
  const sourceNames={sw_l1:"申万一级行业",sw_l2:"申万二级行业",eastmoney:"东方财富行业"};
  const statusNames={healthy:"正常",degraded:"部分可用",unavailable:"不可用",unknown:"未检查"};
  const levelNames={strong:"强势",bullish:"偏强",neutral:"中性",weak:"偏弱",bearish:"弱势"};
  const escapeHtml=value=>String(value??"").replace(/[&<>'"]/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
  const formatTime=value=>value?new Date(value).toLocaleString("zh-CN",{hour12:false}):"—";
  function scoreClass(value){const score=Number(value);if(score>=70)return"score-70";if(score>=60)return"score-60";if(score>=50)return"score-50";if(score>=40)return"score-40";if(score>=30)return"score-30";return"score-low"}

  function createDashboard(options){
    const doc=options.document,fetcher=options.fetch,setTimer=options.setTimeout||setTimeout;
    const elements={
      rows:doc.getElementById("sector-rows"),statuses:doc.getElementById("source-statuses"),
      button:doc.getElementById("refresh-sectors"),error:doc.getElementById("pulse-error"),
      tradeDate:doc.getElementById("last-trade-date"),lastRefresh:doc.getElementById("last-refresh"),
      refreshState:doc.getElementById("refresh-state"),count:doc.getElementById("sector-count")
    };
    let refreshing=false;
    function showError(message){elements.error.textContent=message||"";elements.error.hidden=!message}
    async function request(url,init){const response=await fetcher(url,init);let data={};try{data=await response.json()}catch(_){data={}}if(!response.ok)throw new Error(data.detail?.message||data.detail||`HTTP ${response.status}`);return data}
    function renderSectors(data){
      const items=data.items||[];elements.tradeDate.textContent=data.trade_date||data.latest_trade_date||"—";elements.count.textContent=`${items.length} 个行业`;
      elements.lastRefresh.textContent=items.length?formatTime(items.reduce((latest,item)=>!latest||item.updated_at>latest?item.updated_at:latest,null)):"—";
      if(!items.length){elements.rows.innerHTML='<tr><td colspan="5" class="empty-state">暂无行业数据。</td></tr>';return}
      elements.rows.innerHTML=items.map(item=>`<tr><td>${escapeHtml(item.sector_name)}</td><td><span class="trend-score ${scoreClass(item.trend_score)}">${escapeHtml(item.trend_score)}/70</span></td><td><span class="trend-level">${escapeHtml(levelNames[item.trend_level]||item.trend_level)}</span></td><td>${formatTime(item.updated_at)}</td><td>${escapeHtml(sourceNames[item.source]||item.source)}</td></tr>`).join("");
    }
    function renderHealth(data){const items=data.items||[];elements.statuses.innerHTML=items.map(item=>`<div class="source-status-card"><span class="source-name">${escapeHtml(item.display_name||sourceNames[item.source]||item.source)}</span><span class="source-state ${escapeHtml(item.status)}">${escapeHtml(statusNames[item.status]||item.status)}</span></div>`).join("")||'<div class="loading-card">暂无数据源状态。</div>'}
    async function load(){
      try{const [sectors,health]=await Promise.all([request("/api/market-pulse/sectors"),request("/api/data-source-health")]);renderSectors(sectors);renderHealth(health);showError("");return{sectors,health}}
      catch(error){showError(`加载失败：${error.message}`);elements.rows.innerHTML='<tr><td colspan="5" class="empty-state error-state">数据加载失败。</td></tr>';throw error}
    }
    function setRefreshing(value){refreshing=value;elements.button.disabled=value;elements.button.textContent=value?"刷新中...":"刷新板块数据"}
    async function poll(jobId){
      try{const job=await request(`/api/market-pulse/refresh/${jobId}`);elements.refreshState.textContent=`${job.status} ${Number(job.progress||0).toFixed(0)}%`;if(terminal.has(job.status)){if(job.status==="failed")throw new Error(job.error_message||"刷新失败");await load();setRefreshing(false);if(job.status==="partial")showError(job.error_message||"刷新部分完成");return job}setTimer(()=>poll(jobId),2000);return job}
      catch(error){setRefreshing(false);showError(`刷新失败：${error.message}`);throw error}
    }
    async function refresh(){if(refreshing)return null;setRefreshing(true);showError("");elements.refreshState.textContent="queued 0%";try{const job=await request("/api/market-pulse/refresh",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source:"sw_l1"})});return await poll(job.job_id)}catch(error){setRefreshing(false);showError(`刷新失败：${error.message}`);throw error}}
    function init(){elements.button.addEventListener("click",()=>refresh().catch(()=>{}));return load()}
    return{init,load,refresh,poll,renderSectors,renderHealth,isRefreshing:()=>refreshing};
  }
  const api={createDashboard,scoreClass};
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  if(root){root.MarketPulseDashboard=api;if(root.document&&!root.__MARKET_PULSE_TEST__)root.document.addEventListener("DOMContentLoaded",()=>createDashboard({document:root.document,fetch:root.fetch.bind(root),setTimeout:root.setTimeout.bind(root)}).init().catch(()=>{}))}
})(typeof window!=="undefined"?window:null);
