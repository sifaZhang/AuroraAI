from datetime import date

import pytest

from backend.data_sources.date_normalizer import normalize_date
from backend.data_sources.errors import ProviderValidationError
from backend.data_sources.symbol_normalizer import normalize_symbol, to_gm_symbol, to_tushare_symbol


@pytest.mark.parametrize("raw,canonical", [
    ("600519.SH", "600519.SH"), ("SHSE.600519", "600519.SH"),
    ("000001.SZ", "000001.SZ"), ("430047.BJ", "430047.BJ"),
])
def test_symbol_conversion_preserves_existing_project_canonical(raw, canonical):
    assert normalize_symbol(raw) == canonical
    assert to_tushare_symbol(raw) == canonical


def test_gm_conversion_and_unknown_exchange():
    assert to_gm_symbol("600519.SH") == "SHSE.600519"
    with pytest.raises(ValueError):
        normalize_symbol("600519.XX")


def test_date_normalization_is_strict_and_provider_neutral():
    assert normalize_date("20260730") == date(2026, 7, 30)
    assert normalize_date("2026/07/30") == date(2026, 7, 30)
    assert normalize_date("") is None
    with pytest.raises(ProviderValidationError):
        normalize_date("not-a-date")
