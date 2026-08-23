from __future__ import annotations

import pytest

from backend.research.llm.deepseek import DEFAULT_DEEPSEEK_MODEL, get_deepseek_model


@pytest.mark.parametrize(("role", "variable"), [
    ("analyst", "DEEPSEEK_ANALYST_MODEL"),
    ("skeptic", "DEEPSEEK_SKEPTIC_MODEL"),
    ("valuation", "DEEPSEEK_VALUATION_MODEL"),
    ("portfolio", "DEEPSEEK_PORTFOLIO_MODEL"),
    ("review", "DEEPSEEK_REVIEW_MODEL"),
])
def test_role_specific_model_has_priority(monkeypatch, role, variable):
    monkeypatch.setenv("DEEPSEEK_MODEL", "fallback")
    monkeypatch.setenv(variable, f"{role}-model")
    assert get_deepseek_model(role) == f"{role}-model"


def test_role_model_falls_back_to_shared_then_default(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_ANALYST_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_MODEL", "shared-model")
    assert get_deepseek_model("analyst") == "shared-model"
    monkeypatch.delenv("DEEPSEEK_MODEL")
    assert get_deepseek_model("analyst") == DEFAULT_DEEPSEEK_MODEL


def test_unknown_role_fails_explicitly():
    with pytest.raises(ValueError, match="Unsupported DeepSeek role"):
        get_deepseek_model("unknown")
