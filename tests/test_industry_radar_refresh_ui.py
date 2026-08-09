import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "industry-radar.js"


def _run_node(fetch_body: str) -> None:
    program = f"""
const assert = require('assert');
global.setTimeout = () => 1;
const {{ createRadar }} = require({SCRIPT.as_posix()!r});
const ids = {{}};
for (const id of ['sector-rows','radar-error','industry-scroll-top','industry-table-wrap',
  'industry-refresh-status','industry-refresh-button','industry-search','industry-sort',
  'industry-order','detail-close','industry-detail','industry-trade-date']) {{
  ids[id] = {{ id, disabled:false, textContent:'', hidden:false, firstElementChild:{{style:{{}}}},
    onclick:null, oninput:null, onchange:null, close(){{}}, showModal(){{}} }};
}}
const doc = {{
  getElementById: id => ids[id],
  querySelector: selector => selector === '.industry-table' ? {{scrollWidth:1240}} : null,
  querySelectorAll: () => [],
  createElement: () => ({{appendChild(){{}}, firstChild:{{}}, style:{{}}}}),
}};
const calls = [];
const radar = createRadar({{document:doc, fetch: async (url, options) => {{
  calls.push({{url, options}}); {fetch_body}
}}}});
(async () => {{
  await radar.triggerRefresh();
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].url, '/api/industry/refresh');
  assert.strictEqual(calls[0].options.method, 'POST');
  assert.strictEqual(calls[0].options.body, '{{}}');
  {"assert.strictEqual(ids['industry-refresh-button'].disabled, true); assert.strictEqual(ids['industry-refresh-button'].textContent, '补齐中…');" if 'accepted' in fetch_body else "assert.strictEqual(ids['industry-refresh-button'].disabled, false); assert.strictEqual(ids['industry-refresh-button'].textContent, '重试补齐');"}
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    subprocess.run(["node", "-e", program], check=True, text=True, encoding="utf-8", errors="replace")


def test_refresh_button_posts_and_immediately_shows_loading_state():
    _run_node("return {ok:true, json:async () => ({status:'accepted'})};")


def test_refresh_button_recovers_after_api_failure():
    _run_node("throw Error('network down');")


def test_successful_refresh_reloads_the_industry_trade_date():
    program = f"""
const assert = require('assert');
global.setTimeout = () => 1;
const {{ createRadar }} = require({SCRIPT.as_posix()!r});
const ids = {{}};
for (const id of ['sector-rows','radar-error','industry-scroll-top','industry-table-wrap',
  'industry-refresh-status','industry-refresh-button','industry-search','industry-sort',
  'industry-order','detail-close','industry-detail','industry-trade-date']) {{
  ids[id] = {{ id, disabled:false, textContent:'', hidden:false, value:'', firstElementChild:{{style:{{}}}},
    onclick:null, oninput:null, onchange:null, appendChild(){{}}, close(){{}}, showModal(){{}} }};
}}
const doc = {{getElementById:id=>ids[id], querySelector:s=>s==='.industry-table'?{{scrollWidth:1240}}:null,
  querySelectorAll:()=>[], createElement:()=>({{firstChild:null,appendChild(child){{this.firstChild??=child;}},style:{{}}}})}};
const responses = [
  {{status:'accepted'}},
  {{is_running:false,is_latest:true,run_status:'success',target_trade_date:'2026-08-07',missing_trade_dates:[]}},
  {{trade_date:'2026-08-07',items:[{{industry_code:'850111',industry_name:'三级',total_score:80}}]}},
];
const radar=createRadar({{document:doc,fetch:async()=>({{ok:true,json:async()=>responses.shift()}})}});
(async()=>{{await radar.triggerRefresh(); await radar.checkRefresh();
 assert.strictEqual(ids['industry-trade-date'].textContent,'2026-08-07');
 assert.strictEqual(ids['industry-refresh-status'].textContent,'补齐完成');
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    subprocess.run(["node", "-e", program], check=True, text=True, encoding="utf-8", errors="replace")
