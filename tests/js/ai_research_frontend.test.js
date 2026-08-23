"use strict";
const assert = require("assert");
const {confidence, filterItems, signalGap} = require("../../frontend/ai-research.js");

const rows = [
  {symbol:"000858.SZ",company_name:"五粮液",yield_signal:"heavy",research_decision:"watch",decision_confidence:.4},
  {symbol:"000423.SZ",company_name:"东阿阿胶",yield_signal:"heavy",research_decision:"watch",decision_confidence:0},
  {symbol:"600000.SH",company_name:"Matched",yield_signal:"entry",research_decision:"entry",decision_confidence:.8},
];

assert.strictEqual(confidence(.4), "40%");
assert.strictEqual(confidence(0), "0%");
assert.strictEqual(signalGap(rows[0]), "Heavy → Watch");
assert.strictEqual(signalGap(rows[2]), "Matched");
assert.deepStrictEqual(filterItems(rows,{symbol:"000858",decision:"all",yieldSignal:"all",disagreements:false}).map(x=>x.symbol),["000858.SZ"]);
assert.strictEqual(filterItems(rows,{symbol:"",decision:"watch",yieldSignal:"all",disagreements:false}).length,2);
assert.strictEqual(filterItems(rows,{symbol:"",decision:"all",yieldSignal:"heavy",disagreements:false}).length,2);
assert.strictEqual(filterItems(rows,{symbol:"",decision:"all",yieldSignal:"all",disagreements:true}).length,2);
assert.strictEqual(filterItems(rows,{symbol:"missing",decision:"all",yieldSignal:"all",disagreements:false}).length,0);
assert.deepStrictEqual(filterItems(rows,{symbol:"",decision:"all",yieldSignal:"all",disagreements:false}).map(x=>x.symbol),["600000.SH","000858.SZ","000423.SZ"]);
console.log("ai research frontend tests passed");
