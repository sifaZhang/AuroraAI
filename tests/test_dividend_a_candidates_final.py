import pandas as pd

from backend.dividend.dividend_candidate_rules import FINAL_CSV_COLUMNS
from backend.dividend.final_candidate_service import build_final_candidates


def _row(symbol, name, kind):
    return {"market":"CN", "symbol":symbol, "company_name":name, "industry_level_1":"测试", "industry_level_2":"测试", "monopoly_type":kind, "target_year_1_dps":1, "target_year_2_dps":1, "target_year_3_dps":1, "three_year_average_dps":1, "latest_to_average_ratio":1, "risk_note":""}


def test_explicit_mobile_exemption_and_core_bank_are_included():
    review = pd.DataFrame([_row("601398.SH", "工商银行", "banking_license")])
    supplement = pd.DataFrame([_row("600941.SH", "中国移动", "telecom_network")])
    result, summary = build_final_candidates(review, supplement)
    assert set(result["final_status"]) == {"included"}
    assert result.loc[result.symbol.eq("600941.SH"), "manual_override"].item()
    assert summary["listing_age_exemptions"] == 1


def test_non_core_bank_and_missing_financial_data_require_review():
    review = pd.DataFrame([_row("002142.SZ", "宁波银行", "banking_license"), _row("600012.SH", "皖通高速", "toll_road_concession"), _row("600125.SH", "铁龙物流", "railway_network")])
    result, _ = build_final_candidates(review)
    assert set(result.final_status) == {"review_required"}
    assert result.loc[result.symbol.eq("600012.SH"), "concession_review_required"].item()
    assert result.loc[result.symbol.eq("600125.SH"), "railway_operator_review_required"].item()


def test_oil_gas_is_cyclical_and_steel_is_excluded_and_sort_is_stable():
    review = pd.DataFrame([_row("601857.SH", "中国石油", "oil_gas_resource"), _row("600282.SH", "南钢股份", "unknown"), _row("600028.SH", "中国石化", "oil_gas_resource")])
    result, _ = build_final_candidates(review)
    oil = result[result.symbol.eq("601857.SH")].iloc[0]
    steel = result[result.symbol.eq("600282.SH")].iloc[0]
    assert oil.final_status == "included" and oil.stability_subtype == "resource_monopoly_cyclical"
    assert steel.final_status == "excluded" and steel.final_reason == "excluded_industry"
    assert list(result.columns) == FINAL_CSV_COLUMNS
    assert set(result.final_status) <= {"included", "review_required", "excluded"}


def test_normal_recent_listing_is_not_a_listing_age_exemption():
    review = pd.DataFrame([_row("600001.SH", "普通新股", "telecom_network")])
    result, summary = build_final_candidates(review)
    assert result.loc[0, "manual_override"] == ""
    assert summary["listing_age_exemptions"] == 0
