from backend.research.cost_meter import capture_costs
from backend.research.llm.deepseek import DeepSeekProvider


class Response:
    def raise_for_status(self): pass
    def json(self):
        return {"model": "deepseek-v4-flash", "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
                          "prompt_cache_hit_tokens": 400, "prompt_cache_miss_tokens": 600}}


class Client:
    def post(self, *_args, **_kwargs): return Response()


def test_deepseek_response_usage_is_recorded_once_without_extra_request():
    provider = DeepSeekProvider(api_key="secret", client=Client(), role="valuation")
    with capture_costs() as meter:
        result = provider.generate_structured(system_prompt="system", user_prompt="user")
    summary = meter.summary()
    assert result.usage["prompt_cache_hit_tokens"] == 400
    assert summary["actual_llm_calls"] == 1
    assert summary["agent_usage"]["valuation"]["completion_tokens"] == 200
