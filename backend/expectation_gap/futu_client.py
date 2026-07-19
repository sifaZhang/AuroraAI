from __future__ import annotations

import math
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class CollectionResult:
    status: str
    data: dict[str, Any] | None = None
    error: str | None = None
    raw: Any = None


class SlidingWindowLimiter:
    def __init__(self, limit: int = 28, window_seconds: float = 30.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._calls: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self._calls and now - self._calls[0] >= self.window_seconds:
            self._calls.popleft()
        if len(self._calls) >= self.limit:
            time.sleep(max(0.0, self.window_seconds - (now - self._calls[0])))
        now = time.monotonic()
        while self._calls and now - self._calls[0] >= self.window_seconds:
            self._calls.popleft()
        self._calls.append(now)


def _valid_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


class FutuResearchClient:
    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or os.getenv("FUTU_HOST", "127.0.0.1")
        self.port = port or int(os.getenv("FUTU_PORT", "11111"))
        self.max_retries = int(os.getenv("FUTU_MAX_RETRIES", "3"))
        limit = int(os.getenv("FUTU_REQUESTS_PER_30_SECONDS", "28"))
        self._limiter = SlidingWindowLimiter(limit)
        self._context = None

    def __enter__(self) -> "FutuResearchClient":
        self._connect()
        return self

    def _connect(self) -> None:
        try:
            from futu import OpenQuoteContext
            self._context = OpenQuoteContext(host=self.host, port=self.port)
        except Exception as exc:
            raise ConnectionError(
                f"无法连接富途 OpenD，请确认 OpenD 已启动、已登录且监听 {self.host}:{self.port}。原始错误: {exc}"
            ) from exc

    def _reconnect(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None
        self._connect()

    def __exit__(self, *_: Any) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None

    def global_state(self) -> CollectionResult:
        return self._call(lambda: self._context.get_global_state(), rate_limited=False)

    def hk_security_pool(self):
        from futu import Market, SecurityType
        return self._call(lambda: self._context.get_stock_basicinfo(Market.HK, SecurityType.STOCK), rate_limited=False)

    def security_type_count(self, security_type: str) -> int | None:
        from futu import Market
        result = self._call(lambda: self._context.get_stock_basicinfo(Market.HK, security_type), rate_limited=False)
        return len(result.raw) if result.status == "success" and hasattr(result.raw, "__len__") else None

    def batch_snapshots(self, codes: list[str], batch_size: int = 200) -> dict[str, CollectionResult]:
        output: dict[str, CollectionResult] = {}
        for start in range(0, len(codes), batch_size):
            chunk = codes[start:start + batch_size]
            result = self._call(lambda chunk=chunk: self._context.get_market_snapshot(chunk), rate_limited=False)
            if result.status != "success":
                for code in chunk:
                    output[code] = CollectionResult(result.status, error=result.error)
                continue
            frame = result.raw
            returned = {row["code"]: row for _, row in frame.iterrows()}
            for code in chunk:
                row = returned.get(code)
                price = _valid_positive(row.get("last_price")) if row is not None else None
                output[code] = (CollectionResult("success", {"last_price": price, "price_time": row.get("update_time")})
                                if price is not None else CollectionResult("no_data"))
        return output

    def snapshot(self, code: str) -> CollectionResult:
        result = self._call(lambda: self._context.get_market_snapshot([code]), rate_limited=False)
        if result.status != "success":
            return result
        frame = result.raw
        if frame is None or frame.empty:
            return CollectionResult("no_data", raw=frame)
        row = frame.iloc[0].to_dict()
        if row.get("code") != code:
            return CollectionResult("error", error=f"返回代码不匹配: {row.get('code')} != {code}", raw=frame)
        price = _valid_positive(row.get("last_price"))
        if price is None:
            return CollectionResult("no_data", raw=frame)
        return CollectionResult("success", {"last_price": price, "price_time": row.get("update_time")}, raw=frame)

    def morningstar(self, code: str) -> CollectionResult:
        result = self._call(lambda: self._context.get_research_morningstar_report(code), rate_limited=True)
        if result.status != "success":
            return result
        payload = result.raw
        fair_value = _valid_positive(payload.get("fair_value")) if isinstance(payload, dict) else None
        if fair_value is None:
            return CollectionResult("no_data", raw=payload)
        return CollectionResult("success", {
            "fair_value": fair_value,
            "star_rating": payload.get("star_rating"),
            "rating_type": payload.get("rating_type"),
            "data_date": payload.get("star_update_time_str"),
        }, raw=payload)

    def analyst(self, code: str) -> CollectionResult:
        result = self._call(lambda: self._context.get_research_analyst_consensus(code), rate_limited=True)
        if result.status != "success":
            return result
        payload = result.raw
        average = _valid_positive(payload.get("average")) if isinstance(payload, dict) else None
        if average is None:
            return CollectionResult("no_data", raw=payload)
        count = payload.get("total")
        if count is not None and int(count) < 0:
            return CollectionResult("error", error="分析师人数为负数", raw=payload)
        return CollectionResult("success", {
            "average": average,
            "highest": _valid_positive(payload.get("highest")),
            "lowest": _valid_positive(payload.get("lowest")),
            "total": int(count) if count is not None else None,
            "rating": payload.get("rating"),
            "data_date": payload.get("update_time_str"),
        }, raw=payload)

    def _call(self, function: Callable[[], tuple[int, Any]], rate_limited: bool) -> CollectionResult:
        if self._context is None:
            return CollectionResult("error", error="OpenD 客户端尚未连接")
        backoffs = (2, 5, 10)
        for attempt in range(self.max_retries + 1):
            if rate_limited:
                self._limiter.wait()
            try:
                ret, payload = function()
            except Exception as exc:
                error = str(exc)
            else:
                if ret == 0:
                    is_empty_mapping = isinstance(payload, dict) and not payload
                    if payload is None or (hasattr(payload, "empty") and payload.empty) or is_empty_mapping:
                        return CollectionResult("no_data", raw=payload)
                    return CollectionResult("success", raw=payload)
                error = str(payload)
            retryable = any(token in error.lower() for token in ("频率", "frequency", "rate limit", "timeout", "断开", "disconnect", "connection", "连接"))
            if not retryable or attempt >= self.max_retries:
                return CollectionResult(self._classify_error(error), error=error)
            if self._classify_error(error) == "connection_error":
                try:
                    self._reconnect()
                except ConnectionError as exc:
                    error = str(exc)
            time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
        return CollectionResult("error", error="未知富途接口错误")

    @staticmethod
    def _classify_error(error: str) -> str:
        lowered = error.lower()
        if any(token in lowered for token in ("permission", "权限", "无权")):
            return "permission_denied"
        if any(token in lowered for token in ("frequency", "rate limit", "频率", "限频")):
            return "rate_limited"
        if any(token in lowered for token in ("disconnect", "connection", "timeout", "断开", "连接")):
            return "connection_error"
        return "connection_error"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
