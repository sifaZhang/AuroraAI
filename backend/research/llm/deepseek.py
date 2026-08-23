from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from .base import GenerationResult, LlmProviderError


DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"


@dataclass
class DeepSeekProvider:
    api_key: str
    model: str = DEFAULT_DEEPSEEK_MODEL
    temperature: float = 0.1
    timeout: float = 90.0
    max_output_tokens: int = 6000
    client: httpx.Client | None = None

    @classmethod
    def from_environment(cls) -> "DeepSeekProvider":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise LlmProviderError("DeepSeek API key is not configured")
        return cls(
            api_key=api_key,
            model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
            temperature=float(os.environ.get("RESEARCH_LLM_TEMPERATURE", "0.1")),
            timeout=float(os.environ.get("RESEARCH_LLM_TIMEOUT", "90")),
            max_output_tokens=int(os.environ.get("RESEARCH_LLM_MAX_OUTPUT_TOKENS", "6000")),
        )

    def generate_structured(self, *, system_prompt: str, user_prompt: str) -> GenerationResult:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        }
        started = time.perf_counter()
        try:
            if self.client is not None:
                response = self.client.post(DEEPSEEK_CHAT_URL, json=payload, headers=self._headers(), timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(DEEPSEEK_CHAT_URL, json=payload, headers=self._headers())
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LlmProviderError("DeepSeek request timed out") from exc
        except httpx.HTTPError as exc:
            raise LlmProviderError(f"DeepSeek API request failed: {exc}") from exc
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmProviderError("DeepSeek API response has no message content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LlmProviderError("DeepSeek API returned empty JSON content")
        usage = data.get("usage") or {}
        return GenerationResult(content=content, provider="deepseek", model=str(data.get("model") or self.model),
                                input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                                latency_ms=round((time.perf_counter() - started) * 1000))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
