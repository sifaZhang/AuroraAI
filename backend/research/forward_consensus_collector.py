from __future__ import annotations

import argparse
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from backend.data_sources.errors import DataSourceError
from backend.data_sources.settings import DataSourceSettings
from backend.data_sources.tushare import TushareClient

from .forward_consensus_schema import (
    ForecastYear, ForwardConsensus, MetricStatistics, RevisionTrend, SourceRecords,
)
from .json_store import write_json_atomic
from .profile_builder import DEFAULT_RESEARCH_ROOT


FIELDS = "ts_code,report_date,report_title,report_type,classify,org_name,author_name,quarter,op_rt,np,eps,rating,create_time"
YEAR_PATTERN = re.compile(r"^(\d{4})Q4$")
_CACHE: dict[tuple[str, str], pd.DataFrame] = {}


def consensus_quality(institution_count: int, *, extended_window: bool = False) -> str:
    if institution_count >= 10: quality = "HIGH"
    elif institution_count >= 5: quality = "MEDIUM"
    elif institution_count >= 2: quality = "LOW"
    else: quality = "INSUFFICIENT"
    if extended_window:
        quality = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW", "INSUFFICIENT": "INSUFFICIENT"}[quality]
    return quality


def compare_ai_to_consensus(valuation: dict[str, Any], consensus: dict[str, Any] | None) -> dict[str, Any]:
    result = {"agreement": "NOT_COMPARABLE", "ai_vs_consensus_pct": None,
              "comparison_basis": "not_comparable", "fiscal_year": None}
    if not consensus or consensus.get("status") != "AVAILABLE": return result
    normalized = valuation.get("normalized_earnings", {})
    match = re.fullmatch(r"FY(\d{4})E", str(normalized.get("period") or ""))
    base, unit = normalized.get("base"), normalized.get("unit")
    if not match or base is None or unit != "CNY_100M": return result
    year = match.group(1); metric = consensus.get("forecast_years", {}).get(year, {}).get("net_profit")
    if not metric or not metric.get("median"): return result
    consensus_cny_100m = float(metric["median"]) / 100_000_000
    difference = (float(base) - consensus_cny_100m) / consensus_cny_100m
    absolute = abs(difference)
    agreement = "ALIGNED" if absolute <= .10 else "MODERATE_GAP" if absolute <= .25 else "LARGE_GAP"
    return {"agreement": agreement, "ai_vs_consensus_pct": difference,
            "comparison_basis": "approximate_net_profit_to_normalized_earnings", "fiscal_year": int(year)}


class ForwardConsensusCollector:
    def __init__(self, *, client: TushareClient | None = None, research_root: Path = DEFAULT_RESEARCH_ROOT,
                 now: Callable[[], datetime] | None = None) -> None:
        settings = DataSourceSettings.from_env()
        self.client = client or TushareClient(settings.tushare_token,
            timeout_seconds=settings.request_timeout_seconds, max_retries=settings.max_retries,
            requests_per_minute=min(settings.requests_per_minute, 1))
        self.research_root = research_root
        self.now = now or (lambda: datetime.now(timezone.utc))

    def collect(self, symbol: str, version: str, *, as_of_date: date) -> ForwardConsensus:
        symbol = symbol.upper(); key = (symbol, as_of_date.isoformat())
        try:
            cached = _CACHE.get(key)
            if cached is None:
                start = (as_of_date - timedelta(days=365)).strftime("%Y%m%d")
                cached = self.client.call("report_rc", ts_code=symbol, start_date=start,
                                          end_date=as_of_date.strftime("%Y%m%d"), fields=FIELDS)
                _CACHE[key] = cached.copy()
            frame = cached.copy()
            return self._aggregate(frame, symbol, version, as_of_date)
        except DataSourceError as exc:
            return self._failure(symbol, version, as_of_date, exc)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            return self._failure(symbol, version, as_of_date, exc, status="SOURCE_ERROR")

    def run(self, symbol: str, version: str, *, as_of_date: date) -> ForwardConsensus:
        result = self.collect(symbol, version, as_of_date=as_of_date)
        folder = self.research_root / result.symbol / "analysis" / version
        write_json_atomic(folder / "forward_consensus.json", result.model_dump(mode="json"))
        return result

    def _aggregate(self, frame: pd.DataFrame, symbol: str, version: str, as_of: date) -> ForwardConsensus:
        required = {"ts_code", "report_date", "report_title", "org_name", "quarter", "op_rt", "np", "eps"}
        if not required <= set(frame.columns):
            raise ValueError(f"report_rc schema missing fields: {', '.join(sorted(required - set(frame.columns)))}")
        total = len(frame); normalized: list[dict[str, Any]] = []; future = malformed = 0
        for raw in frame.to_dict("records"):
            try:
                report_date = datetime.strptime(str(raw["report_date"]), "%Y%m%d").date()
            except (TypeError, ValueError):
                malformed += 1; continue
            if report_date > as_of:
                future += 1; continue
            match = YEAR_PATTERN.fullmatch(str(raw.get("quarter") or ""))
            institution = str(raw.get("org_name") or "").strip()
            if not match or not institution:
                malformed += 1; continue
            values = {}
            for source, target, multiplier in (("np", "net_profit", 10_000), ("op_rt", "revenue", 10_000), ("eps", "eps", 1)):
                value = raw.get(source)
                try: values[target] = float(value) * multiplier if value is not None and not pd.isna(value) else None
                except (TypeError, ValueError): values[target] = None
            normalized.append({"year": int(match.group(1)), "institution": institution,
                "report_date": report_date, "report_title": str(raw.get("report_title") or ""), **values})
        years = {}
        current_year = as_of.year
        for year in sorted({item["year"] for item in normalized if item["year"] >= current_year}):
            rows = [item for item in normalized if item["year"] == year]
            included, window = self._select_window(rows, as_of)
            net_count = len({item["institution"] for item in included if item["net_profit"] is not None})
            if not included: continue
            dates = [item["report_date"] for item in included]
            years[str(year)] = ForecastYear(
                fiscal_year=year, period=f"FY{year}", quality=consensus_quality(net_count, extended_window=window == 180),
                freshness_window_days=window, latest_report_date=max(dates).isoformat(),
                oldest_included_report_date=min(dates).isoformat(),
                median_age_days=statistics.median((as_of - item).days for item in dates),
                fresh_estimate_ratio=sum((as_of - item).days <= 90 for item in dates) / len(dates),
                net_profit=self._statistics(included, "net_profit", "CNY"),
                eps=self._statistics(included, "eps", "CNY_PER_SHARE"),
                revenue=self._statistics(included, "revenue", "CNY"),
                revision=self._revision(rows, included),
            )
        report_dates = [item["report_date"] for item in normalized]
        recent = [item for item in normalized if (as_of - item["report_date"]).days <= 180]
        if years and any(item.quality != "INSUFFICIENT" for item in years.values()): status = "AVAILABLE"
        elif normalized and not recent: status = "STALE"
        elif normalized: status = "NO_COVERAGE"
        else: status = "NO_COVERAGE"
        source = SourceRecords(record_count=total, eligible_annual_record_count=len(normalized),
            unique_institution_count=len({item["institution"] for item in normalized}),
            unique_report_count=len({(item["institution"], item["report_date"], item["report_title"]) for item in normalized}),
            latest_report_date=max(report_dates).isoformat() if report_dates else None,
            oldest_report_date=min(report_dates).isoformat() if report_dates else None,
            future_record_count_excluded=future, malformed_record_count_excluded=malformed)
        return ForwardConsensus(schema_version=1, symbol=symbol, analysis_version=version,
            as_of_date=as_of.isoformat(), source="tushare_report_rc", status=status,
            forecast_years=years, source_records=source, generated_at=self.now().isoformat(timespec="seconds"),
            method="point-in-time-institution-median-v1")

    @staticmethod
    def _select_window(rows: list[dict[str, Any]], as_of: date) -> tuple[list[dict[str, Any]], int | None]:
        for window in (90, 180):
            candidates = [row for row in rows if (as_of - row["report_date"]).days <= window]
            latest: dict[str, dict[str, Any]] = {}
            for row in sorted(candidates, key=lambda item: item["report_date"]): latest[row["institution"]] = row
            if len({row["institution"] for row in latest.values() if row["net_profit"] is not None}) >= 2:
                return list(latest.values()), window
        return [], None

    @staticmethod
    def _statistics(rows: list[dict[str, Any]], field: str, unit: str) -> MetricStatistics | None:
        values = [row[field] for row in rows if row[field] is not None]
        if not values: return None
        median = statistics.median(values)
        outliers = sum(abs(value - median) / abs(median) > .50 for value in values) if median else 0
        return MetricStatistics(unit=unit, median=median, mean=statistics.mean(values), min=min(values), max=max(values),
            std=statistics.pstdev(values), institution_count=len(values), outlier_count=outliers)

    @staticmethod
    def _revision(all_rows: list[dict[str, Any]], included: list[dict[str, Any]]) -> RevisionTrend:
        changes = []
        for latest in included:
            if latest["net_profit"] is None: continue
            prior = [row for row in all_rows if row["institution"] == latest["institution"]
                     and row["net_profit"] is not None and 30 <= (latest["report_date"] - row["report_date"]).days <= 90]
            if not prior: continue
            previous = max(prior, key=lambda item: item["report_date"])
            if previous["net_profit"]: changes.append((latest["net_profit"] - previous["net_profit"]) / abs(previous["net_profit"]))
        return RevisionTrend(supported=len(changes) >= 2, matched_institution_count=len(changes),
            revisions_up=sum(value > .001 for value in changes), revisions_down=sum(value < -.001 for value in changes),
            revisions_unchanged=sum(abs(value) <= .001 for value in changes),
            median_revision_pct=statistics.median(changes) if changes else None)

    def _failure(self, symbol: str, version: str, as_of: date, exc: Exception,
                 status: str = "UNAVAILABLE") -> ForwardConsensus:
        return ForwardConsensus(schema_version=1, symbol=symbol, analysis_version=version,
            as_of_date=as_of.isoformat(), source="tushare_report_rc", status=status,
            source_error_type=type(exc).__name__, source_error_message=str(exc)[:240], forecast_years={},
            source_records=SourceRecords(record_count=0, eligible_annual_record_count=0,
                unique_institution_count=0, unique_report_count=0, latest_report_date=None,
                oldest_report_date=None, future_record_count_excluded=0, malformed_record_count_excluded=0),
            generated_at=self.now().isoformat(timespec="seconds"), method="point-in-time-institution-median-v1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build point-in-time forward earnings consensus")
    parser.add_argument("--symbol", required=True); parser.add_argument("--version", required=True)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    args = parser.parse_args(argv)
    result = ForwardConsensusCollector(research_root=args.research_root).run(args.symbol, args.version, as_of_date=args.as_of)
    print(result.model_dump_json(indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
