"""Second-phase, file-only narrowing for A-class dividend candidates."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .dividend_candidate_rules import (
    CONFIRMED_OPERATORS, CORE_BANK_WHITELIST, FINAL_CSV_COLUMNS,
    LISTING_AGE_EXEMPTIONS, OIL_GAS_WHITELIST, STEEL_AND_METAL_EXCLUSIONS,
    TELECOM_OPERATOR_WHITELIST,
)


def build_final_candidates(review: pd.DataFrame, supplements: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    source = pd.concat([review, supplements], ignore_index=True) if supplements is not None and not supplements.empty else review.copy()
    source = source.drop_duplicates(subset=["symbol"], keep="last")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [_classify(row, generated_at) for _, row in source.iterrows()]
    result = pd.DataFrame(rows, columns=FINAL_CSV_COLUMNS).sort_values(
        ["monopoly_type", "final_status", "symbol"], kind="stable"
    ).reset_index(drop=True)
    summary = {
        "source_candidates": len(review),
        "listing_age_exemptions": int(result["symbol"].isin(LISTING_AGE_EXEMPTIONS).sum()),
        "included": int((result["final_status"] == "included").sum()),
        "review_required": int((result["final_status"] == "review_required").sum()),
        "excluded": int((result["final_status"] == "excluded").sum()),
        "final_preselected": int((result["final_status"] == "included").sum()),
    }
    return result, summary


def _classify(row: pd.Series, generated_at: str) -> dict[str, object]:
    name, symbol, kind = str(row["company_name"]), str(row["symbol"]), str(row["monopoly_type"])
    status, reason, fields = "review_required", "manual_business_model_review", []
    subtype, override, risk = "stable_monopoly", "", str(row.get("risk_note") or "")
    concession = regional = railway = False
    if name in STEEL_AND_METAL_EXCLUSIONS:
        status, reason = "excluded", "excluded_industry"
    elif kind == "banking_license":
        if symbol in CORE_BANK_WHITELIST:
            status, reason = "included", "core_bank_whitelist"
        else:
            reason, fields = "non_core_bank", ["core_bank_review"]
    elif kind == "telecom_network":
        if symbol in TELECOM_OPERATOR_WHITELIST:
            status, reason = "included", "telecom_operator_whitelist"
        else:
            reason, fields = "telecom_operator_confirmation_required", ["telecom_operator_review"]
    elif kind in CONFIRMED_OPERATORS:
        if name in CONFIRMED_OPERATORS[kind]:
            status, reason = "included", "confirmed_operating_company"
        else:
            reason, fields = "operator_confirmation_required", ["operator_business_model_review"]
    elif kind == "oil_gas_resource":
        subtype = "resource_monopoly_cyclical"
        risk = "具有资源和规模壁垒，但盈利及分红受油价和炼化周期影响"
        if symbol in OIL_GAS_WHITELIST:
            status, reason, override = "included", "oil_gas_whitelist", "oil_gas_resource_manual_mapping"
        else:
            reason, fields = "oil_gas_operator_confirmation_required", ["resource_operator_review"]
    elif kind == "toll_road_concession":
        reason, fields, concession = "financial_data_unavailable", ["net_profit_3y", "operating_cash_flow_3y", "concession_review"], True
    elif kind == "regional_gas_concession":
        reason, fields, regional = "financial_data_unavailable", ["net_profit_3y", "operating_cash_flow_3y", "regional_monopoly_review"], True
    elif kind == "railway_network":
        reason, fields, railway = "financial_data_unavailable", ["net_profit_3y", "operating_cash_flow_3y", "railway_operator_review"], True
    return {
        "market": row.get("market", "CN"), "symbol": symbol, "company_name": name,
        "industry_level_1": row.get("industry_level_1", ""), "industry_level_2": row.get("industry_level_2", ""),
        "monopoly_type": kind, "stability_subtype": subtype,
        "target_year_1_dps": row.get("target_year_1_dps"), "target_year_2_dps": row.get("target_year_2_dps"),
        "target_year_3_dps": row.get("target_year_3_dps"), "three_year_average_dps": row.get("three_year_average_dps"),
        "latest_to_average_ratio": row.get("latest_to_average_ratio"), "final_status": status,
        "final_reason": reason, "review_required_fields": ";".join(fields), "risk_note": risk,
        "manual_override": override or LISTING_AGE_EXEMPTIONS.get(symbol, ""), "generated_at": generated_at,
        "concession_review_required": concession, "regional_monopoly_review_required": regional,
        "railway_operator_review_required": railway,
    }
