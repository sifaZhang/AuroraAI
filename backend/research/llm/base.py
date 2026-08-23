from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class LlmProviderError(RuntimeError):
    """A provider configuration, transport, or response error."""


@dataclass(frozen=True)
class GenerationResult:
    content: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


class StructuredProvider(Protocol):
    def generate_structured(self, *, system_prompt: str, user_prompt: str) -> GenerationResult: ...
