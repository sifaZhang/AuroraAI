"""Tushare implementation of the provider-neutral SW industry contract."""

from __future__ import annotations

import time
from datetime import date
from typing import Iterable

from ..contracts import IndustryDataProvider
from ..date_normalizer import normalize_date
from ..errors import ProviderEmptyDataError, ProviderSchemaError, ProviderValidationError
from ..models import IndustryMembership, IndustryNode, ProviderHealth, ProviderResult, utc_now
from ..symbol_normalizer import normalize_symbol
from ..validation import validate_industry_nodes, validate_memberships
from .client import TushareClient

FIELDS = ("l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,"
          "ts_code,name,in_date,out_date,is_new")
REQUIRED = set(FIELDS.split(","))


def _records(frame: object) -> list[dict]:
    if frame is None:
        return []
    columns = set(getattr(frame, "columns", ()))
    if not REQUIRED <= columns:
        missing = sorted(REQUIRED - columns)
        raise ProviderSchemaError(f"Tushare index_member_all missing fields: {missing}")
    return list(frame.to_dict("records"))


def _current(value: object, out_date: date | None) -> bool:
    if value is not None and str(value).strip() not in {"", "nan", "None"}:
        return str(value).strip().lower() in {"1", "y", "yes", "true"}
    return out_date is None


def map_memberships(
    frame: object, *, warnings: list[str] | None = None,
    allow_current_conflicts: bool = False, preserve_exact_duplicates: bool = False,
) -> list[IndustryMembership]:
    result = []
    for row in _records(frame):
        out_date = normalize_date(row.get("out_date"))
        try:
            symbol = normalize_symbol(row.get("ts_code"))
        except ValueError as exc:
            raw_symbol = str(row.get("ts_code") or "").strip().upper()
            if raw_symbol.startswith("T") and raw_symbol.endswith((".SH", ".SZ", ".BJ")):
                if warnings is not None:
                    warnings.append(f"excluded non-equity Tushare member: {raw_symbol}")
                continue
            raise ProviderValidationError("invalid Tushare security symbol") from exc
        result.append(IndustryMembership(
            classification="SW", version="2021", symbol=symbol,
            security_name=None if row.get("name") is None else str(row["name"]).strip() or None,
            level1_code=str(row["l1_code"]).strip().removesuffix(".SI"), level1_name=str(row["l1_name"]).strip(),
            level2_code=str(row["l2_code"]).strip().removesuffix(".SI"), level2_name=str(row["l2_name"]).strip(),
            level3_code=str(row["l3_code"]).strip().removesuffix(".SI"), level3_name=str(row["l3_name"]).strip(),
            in_date=normalize_date(row.get("in_date")), out_date=out_date,
            is_current=_current(row.get("is_new"), out_date), source="tushare",
        ))
    return validate_memberships(
        result, allow_current_conflicts=allow_current_conflicts,
        preserve_exact_duplicates=preserve_exact_duplicates,
    )


def build_nodes(rows: Iterable[IndustryMembership], *, enforce_count_ranges: bool = True) -> list[IndustryNode]:
    nodes: dict[tuple[int, str], IndustryNode] = {}
    for row in rows:
        values = (
            IndustryNode("SW", "2021", row.level1_code, row.level1_name, 1, None, "tushare"),
            IndustryNode("SW", "2021", row.level2_code, row.level2_name, 2, row.level1_code, "tushare"),
            IndustryNode("SW", "2021", row.level3_code, row.level3_name, 3, row.level2_code, "tushare"),
        )
        for node in values:
            key = (node.industry_level, node.industry_code)
            if key in nodes and nodes[key] != node:
                raise ProviderValidationError(f"conflicting Tushare industry node: {node.industry_code}")
            nodes[key] = node
    return validate_industry_nodes(list(nodes.values()), enforce_count_ranges=enforce_count_ranges)


def build_nodes_from_frame(frame: object) -> list[IndustryNode]:
    nodes: dict[tuple[int, str], IndustryNode] = {}
    for row in _records(frame):
        chain = (
            (1, row["l1_code"], row["l1_name"], None),
            (2, row["l2_code"], row["l2_name"], row["l1_code"]),
            (3, row["l3_code"], row["l3_name"], row["l2_code"]),
        )
        for level, raw_code, raw_name, raw_parent in chain:
            code = str(raw_code).strip().removesuffix(".SI")
            parent = None if raw_parent is None else str(raw_parent).strip().removesuffix(".SI")
            node = IndustryNode("SW", "2021", code, str(raw_name).strip(), level, parent, "tushare")
            key = (level, code)
            if key in nodes and nodes[key] != node:
                raise ProviderValidationError(f"conflicting Tushare industry node: {code}")
            nodes[key] = node
    return validate_industry_nodes(list(nodes.values()))


class TushareIndustryProvider(IndustryDataProvider):
    def __init__(self, client: TushareClient, *, enabled: bool = True):
        self.client = client
        self.enabled = enabled

    @property
    def name(self) -> str:
        return "tushare"

    @staticmethod
    def _parameters(classification: str, version: str) -> None:
        if classification != "SW" or version != "2021":
            raise ProviderValidationError("only SW 2021 is supported")

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            from ..errors import ProviderUnavailableError
            raise ProviderUnavailableError("Tushare provider is disabled")

    def _all_frame(self):
        self._ensure_enabled()
        frame = self.client.call("index_member_all", fields=FIELDS)
        rows = _records(frame)
        if len(rows) < 2000:
            return frame
        catalog = self.client.call(
            "index_classify", level="L3", src="SW2021",
            fields="index_code,industry_code",
        )
        columns = set(getattr(catalog, "columns", ()))
        code_field = "index_code" if "index_code" in columns else (
            "industry_code" if "industry_code" in columns else None
        )
        if code_field is None:
            raise ProviderSchemaError("Tushare index_classify missing level-3 code")
        frames = []
        for raw_code in catalog[code_field].dropna().astype(str).unique():
            part = self.client.call("index_member_all", l3_code=raw_code, fields=FIELDS)
            if part is not None and not getattr(part, "empty", True):
                frames.append(part)
        if not frames:
            raise ProviderEmptyDataError("Tushare partitioned memberships are empty")
        try:
            import pandas as pd
            combined = pd.concat(frames, ignore_index=True).drop_duplicates()
        except Exception as exc:
            raise ProviderSchemaError("unable to combine Tushare membership batches") from exc
        return combined

    def _all(self) -> tuple[list[IndustryMembership], tuple[str, ...]]:
        warnings: list[str] = []
        return map_memberships(
            self._all_frame(), warnings=warnings, allow_current_conflicts=True,
            preserve_exact_duplicates=True,
        ), tuple(warnings)

    def _result(self, data, requested_at, warnings=()):
        return ProviderResult(data, self.name, requested_at, utc_now(),
                              0 if data is None else len(data) if isinstance(data, list) else 1,
                              warnings=tuple(warnings))

    def list_industries(self, *, classification: str, version: str,
                        level: int | None = None):
        self._parameters(classification, version)
        if level not in {None, 1, 2, 3}:
            raise ProviderValidationError("industry level must be 1, 2 or 3")
        started = utc_now(); nodes = build_nodes_from_frame(self._all_frame())
        selected = nodes if level is None else [node for node in nodes if node.industry_level == level]
        return self._result(selected, started)

    def list_memberships(self, *, classification: str, version: str,
                         as_of_date: date | None = None, current_only: bool = True):
        self._parameters(classification, version); started = utc_now(); rows, warnings = self._all()
        if as_of_date:
            rows = [row for row in rows if (row.in_date is None or row.in_date <= as_of_date)
                    and (row.out_date is None or row.out_date >= as_of_date)]
        if current_only:
            rows = [row for row in rows if row.is_current]
        if not rows:
            raise ProviderEmptyDataError("no Tushare memberships matched the request")
        return self._result(rows, started, warnings)

    def get_symbol_membership(self, symbol: str, *, classification: str, version: str,
                              as_of_date: date | None = None):
        self._ensure_enabled()
        self._parameters(classification, version)
        canonical = normalize_symbol(symbol); started = utc_now(); warnings: list[str] = []
        frame = self.client.call("index_member_all", ts_code=canonical, fields=FIELDS)
        matches = map_memberships(frame, warnings=warnings)
        if as_of_date:
            matches = [row for row in matches if (row.in_date is None or row.in_date <= as_of_date)
                       and (row.out_date is None or row.out_date >= as_of_date)]
        else:
            matches = [row for row in matches if row.is_current]
        if len(matches) > 1:
            raise ProviderValidationError(f"multiple memberships for {canonical}")
        return self._result(matches[0] if matches else None, started, warnings)

    def list_industry_constituents(self, industry_code: str, *, as_of_date: date | None = None):
        self._ensure_enabled()
        started = utc_now(); warnings: list[str] = []
        code = industry_code.strip().removesuffix(".SI")
        frame = self.client.call("index_member_all", l3_code=f"{code}.SI", fields=FIELDS)
        rows = map_memberships(frame, warnings=warnings)
        if as_of_date:
            rows = [row for row in rows if (row.in_date is None or row.in_date <= as_of_date)
                    and (row.out_date is None or row.out_date >= as_of_date)]
        else:
            rows = [row for row in rows if row.is_current]
        if not rows:
            raise ProviderEmptyDataError(f"no constituents for industry {industry_code}")
        return self._result(rows, started, warnings)

    def health_check(self) -> ProviderHealth:
        if not self.enabled:
            return ProviderHealth(self.name, False, False, None, "disabled", None, {})
        started = time.monotonic()
        try:
            frame = self.client.call("index_member_all", fields=FIELDS)
            rows = _records(frame)
            if not rows:
                raise ProviderEmptyDataError("Tushare memberships are empty")
            try:
                map_memberships(frame, warnings=[])
                limited = len(rows) >= 2000
                status = "degraded" if limited else "healthy"
                membership_status = "degraded" if limited else "healthy"
                error_type = "CompletenessUnverified" if limited else None
            except ProviderValidationError as exc:
                status, membership_status, error_type = "degraded", "degraded", type(exc).__name__
            return ProviderHealth(self.name, True, True, True, status,
                                  round((time.monotonic()-started)*1000),
                                  {"industry_catalog": "healthy", "industry_memberships": membership_status,
                                   "industry_third_constituents": "healthy"}, error_type)
        except Exception as exc:
            from ..errors import ProviderAuthenticationError, ProviderTimeoutError, ProviderUnavailableError
            reachable = not isinstance(exc, (ProviderTimeoutError, ProviderUnavailableError))
            authenticated = False if isinstance(exc, ProviderAuthenticationError) else None
            return ProviderHealth(self.name, True, reachable,
                                  authenticated, "unavailable", round((time.monotonic()-started)*1000),
                                  {"industry_catalog": "unavailable", "industry_memberships": "unavailable",
                                   "industry_third_constituents": "unavailable"}, type(exc).__name__)
