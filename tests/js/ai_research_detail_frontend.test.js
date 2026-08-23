"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const {currentVsBase, groupClaims, percent, rangeModel, renderMarkup, sortRisks} = require("../../frontend/ai-research-detail.js");

const data = {
  symbol:"000423.SZ", company_name:"东阿阿胶", analysis_version:"2026-08-23_v1", research_date:"2026-08-23",
  confidence:{score:60,level:"MEDIUM",source:"calibrated",model_reported:0,
    components:{evidence_completeness:{score:100,weight:.25,reasons:["Complete"]},earnings_visibility:{score:40,weight:.25,reasons:["LOW"]}},
    strengths:["Evidence is complete."],blockers:["Earnings visibility is low."]},
  market:{latest_price:48.35,yield_signal:"heavy",three_year_average_yield:.0476,grade:"A"},
  decision:{research_decision:"watch",decision_confidence:0,decision_cap:"entry",
    decision_rationale:{summary:"Remain on watch.",supporting_points:["Long-term earnings improved."],limiting_points:["Cash flow risk."]},
    valuation_assessment:{status:"FAIR_TO_EXPENSIVE"},
    conflicts:[{topic:"valuation",analyst_view:"Earnings improved.",skeptic_view:"Price is near bull value.",portfolio_manager_resolution:"Remain on watch."}]},
  valuation:{confidence:.35,normalized_earnings:{forecast_quality:"LOW",bear:18,base:22,bull:26,unit:"CNY_100M"},
    multiples:{metric:"PE",bear:10,base:14,bull:18},fair_value_per_share:{bear:30.75,base:41.46,bull:53.68},
    reverse_valuation:{scenarios:[{scenario:"Base",multiple:14,implied_earnings:22,interpretation:"Scenario only."}]},
    dividend_cross_check:{current_yield:.0476,sustainable_dividend:2,sustainable_yield:.04,assessment:"Coverage requires monitoring."}},
  skeptic:{verdict:"CAUTION",recommended_research_cap:"ENTRY",overall_assessment:{confidence:.7},
    major_risks:[{severity:"MEDIUM",category:"industry",statement:"Demand risk",reasoning:"Evidence."},{severity:"HIGH",category:"cash_flow",statement:"OCF risk",reasoning:"Weak cash conversion."}],
    dividend_trap_assessment:{status:"POSSIBLE",confidence:.6,reasons:["Coverage risk"]},
    challenged_claims:[{analyst_claim_id:"C1",severity:"HIGH",status:"WEAKLY_SUPPORTED",reason:"Cash evidence limits it."}]},
  analyst:{provisional_view:{valuation_attractiveness:"neutral",confidence:.6,summary:"Fundamentals improved."},
    investment_thesis:{summary:"Core product remains resilient.",positive_drivers:["Earnings history"],market_concerns:["Receivables"]},
    quality_assessment:{business_quality:{score:8,reason:"Brand"},earnings_quality:{score:7,reason:"Improving"},dividend_quality:{score:6,reason:"Coverage"},governance_quality:{score:7,reason:"Stable"}},
    normalized_earnings:{method:"insufficient_evidence"},claims:[{claim_id:"C1",claim_type:"FACT",statement:"Revenue improved.",confidence:1,evidence_ids:["financial_report:2026H1"]},{claim_id:"C2",claim_type:"INFERENCE",statement:"Recovery may continue.",confidence:.5,evidence_ids:[]}]},
  research_conditions:{upgrade:[{condition:"OCF improves"}],downgrade:[{condition:"Receivables worsen"}],invalidation:[{condition:"Core profitability structurally erodes"}]},
  evidence:{summary_available:false,source_count:10},history:{completed_versions:1},
  technical:{analyst:{model:"deepseek-v4-flash",prompt_version:"fundamental-analyst-v1",input_tokens:10,output_tokens:2,latency_ms:30}}
};

assert.strictEqual(percent(.4), "40%");
assert.strictEqual(percent(0), "0%");
assert.strictEqual(currentVsBase(48.35,41.46).toFixed(3), "0.166");
assert.deepStrictEqual(groupClaims(data.analyst.claims).FACT.map(x=>x.claim_id), ["C1"]);
assert.strictEqual(sortRisks(data.skeptic.major_risks)[0].severity, "HIGH");
assert.ok(rangeModel(30.75,41.46,53.68,60));
assert.strictEqual(rangeModel(null,41.46,53.68,48.35), null);

const markup = renderMarkup(data);
for (const text of ["东阿阿胶","HEAVY","WATCH","ENTRY","60%","MEDIUM","Model-reported Confidence: <strong>0%","FAIR TO EXPENSIVE","Current vs Base Fair Value",
  "Market-Implied Earnings","Dividend Cross-check","OCF risk","Dividend Trap Assessment","Evidence-backed Claims",
  "Revenue improved.","升级条件","降级条件","投资逻辑失效条件","Compressed evidence summary unavailable",
  "Fundamental Analyst did not independently estimate normalized earnings.","Confidence Strengths","Main Confidence Blockers"]) assert.ok(markup.includes(text), text);

const html = fs.readFileSync(path.join(__dirname,"../../frontend/ai-research-detail.html"),"utf8");
assert.ok(html.includes('href="/dividend/ai-research"'));
assert.ok(html.includes('href="/dividend/universe"'));
assert.ok(html.includes('id="detail-error"'));

const overview = fs.readFileSync(path.join(__dirname,"../../frontend/ai-research.js"),"utf8");
const universe = fs.readFileSync(path.join(__dirname,"../../frontend/dividend-universe.js"),"utf8");
assert.ok(overview.includes("detailUrl(item.symbol)"));
assert.ok(universe.includes('/dividend/ai-research/${encodeURIComponent(item.symbol)}'));
console.log("ai research detail frontend tests passed");
