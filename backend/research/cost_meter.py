from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator, Mapping


MILLION = Decimal("1000000")
MODEL_PRICING: dict[str, dict[str, Decimal]] = {
    "deepseek-v4-flash": {
        "cache_hit_input": Decimal("0.02"),
        "cache_miss_input": Decimal("1.00"),
        "output": Decimal("2.00"),
    },
}
DEFAULT_PRICING_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    pricing_basis: str = "fallback_all_prompt_as_cache_miss"

    @classmethod
    def from_api_usage(cls, raw: Mapping[str, Any] | None) -> "LlmUsage":
        data = raw or {}
        def integer(*keys: str) -> int | None:
            for key in keys:
                value = data.get(key)
                if value is not None:
                    try: return max(0, int(value))
                    except (TypeError, ValueError): pass
            return None
        prompt = integer("prompt_tokens") or 0
        completion = integer("completion_tokens") or 0
        total = integer("total_tokens")
        hit = integer("prompt_cache_hit_tokens", "cache_hit_tokens")
        miss = integer("prompt_cache_miss_tokens", "cache_miss_tokens")
        if hit is None or miss is None:
            hit, miss, basis = 0, prompt, "fallback_all_prompt_as_cache_miss"
        else:
            basis = "actual_cache_split"
        return cls(prompt, completion, total if total is not None else prompt + completion, hit, miss, basis)

    def as_dict(self) -> dict[str, int | str]:
        return {"prompt_tokens": self.prompt_tokens, "cache_hit_tokens": self.cache_hit_tokens,
                "cache_miss_tokens": self.cache_miss_tokens, "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens, "pricing_basis": self.pricing_basis}


@dataclass(frozen=True)
class LlmCost:
    input_cost_cny: Decimal
    output_cost_cny: Decimal
    total_cost_cny: Decimal

    def as_dict(self) -> dict[str, float]:
        return {"input_cost_cny": float(self.input_cost_cny), "output_cost_cny": float(self.output_cost_cny),
                "total_cost_cny": float(self.total_cost_cny)}


def calculate_cost(usage: LlmUsage, model: str) -> LlmCost:
    price = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_PRICING_MODEL])
    input_cost = (Decimal(usage.cache_hit_tokens) * price["cache_hit_input"] +
                  Decimal(usage.cache_miss_tokens) * price["cache_miss_input"]) / MILLION
    output_cost = Decimal(usage.completion_tokens) * price["output"] / MILLION
    return LlmCost(input_cost, output_cost, input_cost + output_cost)


class CostMeter:
    def __init__(self) -> None: self.records: list[dict[str, Any]] = []

    def record(self, *, agent: str, model: str, usage: LlmUsage) -> None:
        cost = calculate_cost(usage, model)
        self.records.append({"agent": agent, "model": model, **usage.as_dict(), **cost.as_dict()})

    def summary(self) -> dict[str, Any]:
        fields = ("prompt_tokens", "cache_hit_tokens", "cache_miss_tokens", "completion_tokens", "total_tokens")
        usage = {field: sum(int(item[field]) for item in self.records) for field in fields}
        usage["total_cost_cny"] = float(sum((Decimal(str(item["total_cost_cny"])) for item in self.records), Decimal()))
        agent_usage: dict[str, dict[str, Any]] = {}
        for item in self.records:
            value = agent_usage.setdefault(item["agent"], {field: 0 for field in fields} | {"total_cost_cny": 0.0})
            for field in fields: value[field] += int(item[field])
            value["total_cost_cny"] += item["total_cost_cny"]
        return {"actual_llm_calls": len(self.records), "usage": usage, "agent_usage": agent_usage,
                "agent_costs": {agent: value["total_cost_cny"] for agent, value in agent_usage.items()},
                "calls": list(self.records)}


_ACTIVE_METER: ContextVar[CostMeter | None] = ContextVar("research_cost_meter", default=None)


def activate_cost_meter(meter: CostMeter):
    return _ACTIVE_METER.set(meter)


def deactivate_cost_meter(token: object) -> None:
    _ACTIVE_METER.reset(token)


@contextmanager
def capture_costs() -> Iterator[CostMeter]:
    meter = CostMeter(); token = _ACTIVE_METER.set(meter)
    try: yield meter
    finally: _ACTIVE_METER.reset(token)


def record_response_usage(*, agent: str | None, model: str, usage: Mapping[str, Any] | None) -> None:
    meter = _ACTIVE_METER.get()
    if meter is not None:
        meter.record(agent=agent or "unknown", model=model, usage=LlmUsage.from_api_usage(usage))
