"""AKShare fallback adapter for SW industry catalog and memberships."""

from __future__ import annotations

import time
from datetime import date

from ..contracts import IndustryDataProvider
from ..date_normalizer import normalize_date
from ..errors import (
    ProviderEmptyDataError, ProviderSchemaError, ProviderUnavailableError,
    ProviderValidationError,
)
from ..models import IndustryMembership, IndustryNode, ProviderHealth, ProviderResult, utc_now
from ..symbol_normalizer import normalize_symbol
from ..validation import validate_industry_nodes, validate_memberships


def _column(frame: object, *names: str) -> str:
    columns = tuple(str(value) for value in getattr(frame, "columns", ()))
    for name in names:
        if name in columns:
            return name
    raise ProviderSchemaError(f"AKShare response missing one of fields: {names}")


def _records(frame: object) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        raise ProviderEmptyDataError("AKShare response is empty")
    return list(frame.to_dict("records"))


def _catalog_frame(frame: object, level: int, source: str) -> list[IndustryNode]:
    code_col = _column(frame, "行业代码", "指数代码")
    name_col = _column(frame, "行业名称", "指数名称")
    parent_col = None if level == 1 else _column(frame, "上级行业", "上级行业名称")
    return [IndustryNode(
        "SW", "2021", str(row[code_col]).strip().removesuffix(".SI"),
        str(row[name_col]).strip(), level,
        None if parent_col is None else str(row[parent_col]).strip(), source,
    ) for row in _records(frame)]


def build_catalog(ak: object) -> list[IndustryNode]:
    levels = {
        1: _catalog_frame(ak.sw_index_first_info(), 1, "akshare"),
        2: _catalog_frame(ak.sw_index_second_info(), 2, "akshare"),
        3: _catalog_frame(ak.sw_index_third_info(), 3, "akshare"),
    }
    name_to_code = {
        level: {node.industry_name: node.industry_code for node in nodes}
        for level, nodes in levels.items()
    }
    converted = list(levels[1])
    for level, parent_level in ((2, 1), (3, 2)):
        for node in levels[level]:
            parent_code = name_to_code[parent_level].get(node.parent_code or "")
            if parent_code is None:
                raise ProviderValidationError(f"AKShare parent industry missing: {node.industry_code}")
            converted.append(IndustryNode(
                node.classification, node.version, node.industry_code, node.industry_name,
                node.industry_level, parent_code, node.source,
            ))
    return validate_industry_nodes(converted)


class AKShareIndustryProvider(IndustryDataProvider):
    def __init__(self, ak: object | None = None, *, enabled: bool = True):
        self._ak = ak
        self.enabled = enabled

    @property
    def name(self) -> str:
        return "akshare"

    @property
    def ak(self):
        if self._ak is None:
            try:
                import akshare
                self._ak = akshare
            except Exception as exc:
                raise ProviderUnavailableError("AKShare import failed") from exc
        return self._ak

    @staticmethod
    def _parameters(classification: str, version: str) -> None:
        if classification != "SW" or version != "2021":
            raise ProviderValidationError("only SW 2021 is supported")

    def _result(self, data, requested_at, warnings=()):
        return ProviderResult(data, self.name, requested_at, utc_now(),
                              len(data) if isinstance(data, list) else 0 if data is None else 1,
                              warnings=tuple(warnings))

    def list_industries(self, *, classification: str, version: str,
                        level: int | None = None):
        if not self.enabled:
            raise ProviderUnavailableError("AKShare provider is disabled")
        self._parameters(classification, version)
        if level not in {None, 1, 2, 3}:
            raise ProviderValidationError("industry level must be 1, 2 or 3")
        started = utc_now()
        try:
            nodes = build_catalog(self.ak)
        except (ProviderSchemaError, ProviderValidationError, ProviderEmptyDataError):
            raise
        except Exception as exc:
            raise ProviderUnavailableError(f"AKShare catalog failed: {type(exc).__name__}") from exc
        return self._result(nodes if level is None else [x for x in nodes if x.industry_level == level], started)

    def _constituents(self, industry_code: str) -> list[IndustryMembership]:
        catalog = self.list_industries(classification="SW", version="2021").data
        nodes = {node.industry_code: node for node in catalog}
        industry_code = industry_code.removesuffix(".SI")
        node = nodes.get(industry_code)
        if node is None or node.industry_level != 3:
            raise ProviderValidationError("AKShare constituents require a valid level-3 code")
        parent2 = nodes.get(node.parent_code or "")
        parent1 = nodes.get(parent2.parent_code or "") if parent2 else None
        if not parent1 or not parent2:
            raise ProviderValidationError("incomplete AKShare industry hierarchy")
        try:
            frame = self.ak.sw_index_third_cons(symbol=f"{industry_code}.SI")
        except ValueError as exc:
            raise ProviderSchemaError(
                "AKShare SW level-3 constituent schema changed"
            ) from exc
        except Exception as exc:
            raise ProviderUnavailableError(
                f"AKShare SW level-3 constituents failed: {type(exc).__name__}"
            ) from exc
        code_col = _column(frame, "股票代码", "证券代码")
        name_col = _column(frame, "股票简称", "证券简称", "名称")
        date_col = next((name for name in ("纳入时间", "纳入日期")
                         if name in getattr(frame, "columns", ())), None)
        rows = []
        for raw in _records(frame):
            code = str(raw[code_col]).strip()
            try:
                symbol = normalize_symbol(code)
            except ValueError as exc:
                raise ProviderValidationError("AKShare constituent symbol lacks exchange") from exc
            rows.append(IndustryMembership(
                "SW", "2021", symbol, str(raw[name_col]).strip() or None,
                parent1.industry_code, parent1.industry_name,
                parent2.industry_code, parent2.industry_name,
                node.industry_code, node.industry_name,
                normalize_date(raw.get(date_col)) if date_col else None, None, True, "akshare",
            ))
        return validate_memberships(rows)

    def list_industry_constituents(self, industry_code: str, *, as_of_date: date | None = None):
        if as_of_date is not None:
            raise ProviderValidationError("AKShare does not provide historical membership snapshots")
        started = utc_now(); return self._result(self._constituents(industry_code), started)

    def list_memberships(self, *, classification: str, version: str,
                         as_of_date: date | None = None, current_only: bool = True):
        self._parameters(classification, version)
        if as_of_date is not None or not current_only:
            raise ProviderValidationError("AKShare only provides current memberships")
        started = utc_now()
        third = self.list_industries(classification=classification, version=version, level=3).data
        rows = []
        for node in third:
            rows.extend(self._constituents(node.industry_code))
        return self._result(validate_memberships(rows), started,
                            ("AKShare current snapshot has no historical out dates",))

    def get_symbol_membership(self, symbol: str, *, classification: str, version: str,
                              as_of_date: date | None = None):
        canonical = normalize_symbol(symbol); started = utc_now()
        page = self.list_memberships(classification=classification, version=version,
                                     as_of_date=as_of_date, current_only=True)
        matches = [row for row in page.data if row.symbol == canonical]
        if len(matches) > 1:
            raise ProviderValidationError(f"multiple AKShare memberships for {canonical}")
        return self._result(matches[0] if matches else None, started)

    def health_check(self) -> ProviderHealth:
        if not self.enabled:
            return ProviderHealth(self.name, False, False, None, "disabled", None, {})
        started = time.monotonic()
        try:
            nodes = build_catalog(self.ak)
            status = "healthy"; error = None
            try:
                third = next(node for node in nodes if node.industry_level == 3)
                self._constituents(third.industry_code)
                member_status = "healthy"
            except Exception as exc:
                status, member_status, error = "degraded", "degraded", type(exc).__name__
            return ProviderHealth(self.name, True, True, None, status,
                                  round((time.monotonic()-started)*1000),
                                  {"industry_catalog": "healthy", "industry_memberships": member_status,
                                   "industry_third_constituents": member_status}, error)
        except Exception as exc:
            return ProviderHealth(self.name, True, False, None, "unavailable",
                                  round((time.monotonic()-started)*1000),
                                  {"industry_catalog": "unavailable", "industry_memberships": "unavailable",
                                   "industry_third_constituents": "unavailable"}, type(exc).__name__)
