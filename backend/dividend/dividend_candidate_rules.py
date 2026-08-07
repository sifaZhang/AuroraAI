"""Central configuration for A-class stable dividend candidates."""

from __future__ import annotations

from datetime import date

MIN_LISTING_YEARS = 5
CONTINUOUS_DIVIDEND_YEARS = 3
LATEST_TO_AVERAGE_MIN_RATIO = 0.70
LISTING_AGE_EXEMPTIONS = {
    "600941.SH": "中国移动：企业经营历史和分红历史显著超过A股上市年限",
}

CORE_BANK_WHITELIST = {
    "601398.SH", "601939.SH", "601288.SH", "601988.SH", "601328.SH",
    "601658.SH", "600036.SH", "601166.SH", "601998.SH", "600000.SH",
}
TELECOM_OPERATOR_WHITELIST = {"600941.SH", "601728.SH", "600050.SH"}
OIL_GAS_WHITELIST = {"600028.SH", "601857.SH"}
CONFIRMED_OPERATORS = {
    "hydropower_resource": {"甘肃能源", "华能水电", "桂冠电力", "川投能源", "国投电力", "长江电力"},
    "nuclear_license": {"中国广核", "中国核电"},
}
STEEL_AND_METAL_EXCLUSIONS = {
    "中信特钢", "太钢不锈", "沙钢股份", "久立特材", "金洲管道", "常宝股份",
    "盛德鑫泰", "西宁特钢", "南钢股份", "抚顺特钢", "方大特钢", "甬金股份",
}
MANUAL_CORE_ADDITIONS = {
    "601728.SH": {
        "company_name": "中国电信", "monopoly_type": "telecom_network",
        "stability_subtype": "stable_monopoly", "reason": "全国电信运营商；企业经营历史和分红历史显著长于A股上市历史。",
    },
    "601088.SH": {
        "company_name": "中国神华", "monopoly_type": "integrated_energy_resource",
        "stability_subtype": "resource_monopoly_cyclical", "reason": "煤炭资源、铁路、港口、电力一体化经营，资源和产业链壁垒突出。",
    },
}
FINAL_CSV_COLUMNS = [
    "market", "symbol", "company_name", "industry_level_1", "industry_level_2",
    "monopoly_type", "stability_subtype", "target_year_1_dps", "target_year_2_dps",
    "target_year_3_dps", "three_year_average_dps", "latest_to_average_ratio",
    "final_status", "final_reason", "review_required_fields", "risk_note",
    "manual_override", "generated_at", "concession_review_required",
    "regional_monopoly_review_required", "railway_operator_review_required",
]

CSV_COLUMNS = [
    "market", "symbol", "company_name", "list_date", "listing_years",
    "industry_level_1", "industry_level_2", "industry_source", "stability_category",
    "monopoly_type", "target_year_1", "target_year_1_dps", "target_year_2",
    "target_year_2_dps", "target_year_3", "target_year_3_dps", "three_year_total_dps",
    "three_year_average_dps", "latest_year_dps", "latest_to_average_ratio",
    "dividend_event_count_3y", "candidate_reason", "risk_note", "data_quality_status",
    "generated_at",
]
EXCLUSION_COLUMNS = [
    "market", "symbol", "company_name", "industry", "exclusion_stage",
    "exclusion_reason", "details", "generated_at",
]

# Keywords are deliberately narrow.  An explicit current SW membership is used
# first; these phrases only classify a clear operating industry, never equipment.
A_CLASS_INDUSTRY_RULES = {
    "banking_license": ("银行",),
    "telecom_network": ("电信运营", "通信运营", "移动", "联通", "电信"),
    "hydropower_resource": ("水力发电", "水电运营", "水电"),
    "nuclear_license": ("核力发电", "核电运营", "核电"),
    "oil_gas_resource": ("油气开采", "石油开采", "天然气开采", "综合油气", "油田", "海油"),
    "toll_road_concession": ("高速公路", "收费公路", "高速"),
    "regional_gas_concession": ("城市燃气", "区域燃气", "燃气公用事业", "燃气"),
    "railway_network": ("铁路运输", "铁路运营", "铁路"),
}
A_CLASS_EXCLUDED_INDUSTRIES = (
    "煤炭", "航运", "钢铁", "有色金属", "化工", "房地产", "券商", "火力发电",
    "光伏", "风电", "汽车", "消费电子", "纺织", "医药", "白酒", "通信设备", "核电设备",
)
EXCLUDED_NAME_KEYWORDS = ("设备", "科技", "工程", "建设", "机械", "装备", "服务", "设计", "制造", "油服")
# Current SW labels classify these integrated national oil companies under
# refining/trading.  Keep the business-model exception explicit and auditable.
A_CLASS_COMPANY_MONOPOLY_OVERRIDES = {
    "中国石油": "oil_gas_resource",
    "中国石化": "oil_gas_resource",
}


def target_years(calculation_date: date) -> tuple[int, int, int]:
    last_complete_year = calculation_date.year - 1
    return last_complete_year - 2, last_complete_year - 1, last_complete_year


def classify_industry(level_1: str | None, level_2: str | None, level_3: str | None, company_name: str | None = None) -> str | None:
    text = " ".join(value or "" for value in (level_1, level_2, level_3))
    if company_name in A_CLASS_COMPANY_MONOPOLY_OVERRIDES:
        return A_CLASS_COMPANY_MONOPOLY_OVERRIDES[company_name]
    if any(keyword in text for keyword in A_CLASS_EXCLUDED_INDUSTRIES):
        return None
    # “油田服务” is an oil-service business, not resource extraction.
    if "油服" in text or "油田服务" in text:
        return None
    for monopoly_type, keywords in A_CLASS_INDUSTRY_RULES.items():
        if any(keyword in text for keyword in keywords):
            return monopoly_type
    if any(keyword in text for keyword in EXCLUDED_NAME_KEYWORDS):
        return None
    return None
