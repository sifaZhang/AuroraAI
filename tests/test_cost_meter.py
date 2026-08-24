from decimal import Decimal

from backend.research.cost_meter import CostMeter, LlmUsage, calculate_cost


def test_actual_cache_split_pricing_uses_decimal_formula():
    usage = LlmUsage.from_api_usage({"prompt_tokens": 1_000_000, "completion_tokens": 100_000,
                                     "total_tokens": 1_100_000, "prompt_cache_hit_tokens": 600_000,
                                     "prompt_cache_miss_tokens": 400_000})
    cost = calculate_cost(usage, "deepseek-v4-flash")
    assert usage.pricing_basis == "actual_cache_split"
    assert cost.input_cost_cny == Decimal("0.412")
    assert cost.output_cost_cny == Decimal("0.2")
    assert cost.total_cost_cny == Decimal("0.612")


def test_missing_cache_split_is_conservative_and_missing_fields_are_safe():
    usage = LlmUsage.from_api_usage({"prompt_tokens": 1_000_000, "completion_tokens": 100_000})
    assert (usage.cache_hit_tokens, usage.cache_miss_tokens, usage.pricing_basis) == (0, 1_000_000, "fallback_all_prompt_as_cache_miss")
    assert calculate_cost(usage, "model-alias").total_cost_cny == Decimal("1.2")
    empty = LlmUsage.from_api_usage({})
    assert empty.total_tokens == 0 and calculate_cost(empty, "deepseek-v4-flash").total_cost_cny == 0


def test_meter_retains_per_agent_records_and_company_aggregation():
    meter = CostMeter()
    meter.record(agent="analyst", model="deepseek-v4-flash", usage=LlmUsage(1000, 200, 1200, 400, 600, "actual_cache_split"))
    meter.record(agent="valuation", model="deepseek-v4-flash", usage=LlmUsage(500, 100, 600, 0, 500, "fallback_all_prompt_as_cache_miss"))
    summary = meter.summary()
    assert summary["actual_llm_calls"] == 2
    assert summary["usage"]["prompt_tokens"] == 1500
    assert set(summary["agent_usage"]) == {"analyst", "valuation"}
    assert summary["agent_costs"]["valuation"] > 0
