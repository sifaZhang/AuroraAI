from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .final_candidate_service import build_final_candidates
from .dividend_candidate_rules import STEEL_AND_METAL_EXCLUSIONS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Narrow A-class dividend candidates for manual review.")
    parser.add_argument("--input", default="exports/dividend/dividend_a_candidates_review.csv")
    parser.add_argument("--supplements-input")
    parser.add_argument("--exclusions-input", default="exports/dividend/dividend_a_candidate_exclusions.csv")
    parser.add_argument("--output", default="exports/dividend/dividend_a_candidates_final.csv")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review = pd.read_csv(args.input, encoding="utf-8-sig")
    supplements = pd.read_csv(args.supplements_input, encoding="utf-8-sig") if args.supplements_input else pd.DataFrame()
    exclusions = pd.read_csv(args.exclusions_input, encoding="utf-8-sig")
    steel = exclusions[exclusions["company_name"].isin(STEEL_AND_METAL_EXCLUSIONS)].copy()
    if not steel.empty:
        steel = steel.rename(columns={"industry": "industry_level_2"})
        steel["industry_level_1"] = ""
        steel["monopoly_type"] = "unknown"
        steel["target_year_1_dps"] = None; steel["target_year_2_dps"] = None; steel["target_year_3_dps"] = None
        steel["three_year_average_dps"] = None; steel["latest_to_average_ratio"] = None; steel["risk_note"] = ""
        supplements = pd.concat([supplements, steel], ignore_index=True)
    result, summary = build_final_candidates(review, supplements)
    for key, value in summary.items(): print(f"{key}: {value}")
    if not args.dry_run:
        output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(output, index=False, encoding="utf-8-sig")
        print(f"final_file: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
