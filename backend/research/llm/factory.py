from __future__ import annotations

import os

from .base import StructuredProvider
from .deepseek import DeepSeekProvider


def create_provider(name: str | None = None) -> StructuredProvider:
    provider = (name or os.environ.get("RESEARCH_LLM_PROVIDER", "deepseek")).lower()
    if provider == "deepseek":
        return DeepSeekProvider.from_environment()
    raise ValueError(f"Unsupported research LLM provider: {provider}")
