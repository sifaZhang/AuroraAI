"""Lazy, throttled and retrying Tushare Pro client."""

from __future__ import annotations

import socket
import threading
import time
from queue import Queue, Empty
from typing import Any

from ..errors import (
    DataSourceError, ProviderAuthenticationError, ProviderPermissionError,
    ProviderRateLimitError, ProviderSchemaError, ProviderTimeoutError,
    ProviderUnavailableError, ProviderValidationError,
)


class TushareClient:
    def __init__(self, token: str, *, timeout_seconds: float = 30,
                 max_retries: int = 2, requests_per_minute: int = 180,
                 sdk: object | None = None, sleeper=time.sleep):
        self._token = token.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.requests_per_minute = requests_per_minute
        self._sdk = sdk
        self._pro: object | None = None
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._last_request = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _load(self) -> object:
        if not self._token:
            raise ProviderAuthenticationError("Tushare token is not configured")
        if self._pro is None:
            try:
                sdk = self._sdk
                if sdk is None:
                    import tushare as sdk  # type: ignore[no-redef]
                # Passing the token directly avoids Tushare SDK's global
                # ``set_token`` side effect, which writes ``~/tk.csv``.
                try:
                    self._pro = sdk.pro_api(self._token)
                except TypeError:
                    # Minimal injected test SDKs may expose a no-argument factory.
                    self._pro = sdk.pro_api()
            except Exception as exc:
                raise self._translate(exc) from exc
        return self._pro

    def _throttle(self) -> None:
        interval = 60.0 / self.requests_per_minute
        with self._lock:
            delay = interval - (time.monotonic() - self._last_request)
            if delay > 0:
                self._sleeper(delay)
            self._last_request = time.monotonic()

    def call(self, endpoint: str, **params: Any):
        if endpoint.startswith("_"):
            raise ProviderValidationError("invalid Tushare endpoint")
        last: DataSourceError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                pro = self._load()
                method = getattr(pro, endpoint, None)
                if not callable(method):
                    raise ProviderSchemaError(f"Tushare endpoint is unavailable: {endpoint}")
                self._throttle()
                return self._invoke_with_timeout(method, params)
            except DataSourceError as exc:
                last = exc
                retryable = isinstance(last, (ProviderUnavailableError, ProviderTimeoutError,
                                               ProviderRateLimitError))
                if not retryable or attempt >= self.max_retries:
                    raise
                self._sleeper(min(2 ** attempt, 5))
            except Exception as exc:
                last = self._translate(exc)
                retryable = isinstance(last, (ProviderUnavailableError, ProviderTimeoutError,
                                               ProviderRateLimitError))
                if not retryable or attempt >= self.max_retries:
                    raise last from exc
                self._sleeper(min(2 ** attempt, 5))
        raise last or ProviderUnavailableError("Tushare request failed")

    def _invoke_with_timeout(self, method, params):
        queue: Queue = Queue(maxsize=1)

        def invoke():
            try:
                queue.put((True, method(**params)))
            except BaseException as exc:  # transported back to the caller thread
                queue.put((False, exc))

        worker = threading.Thread(target=invoke, daemon=True, name="tushare-request")
        worker.start()
        try:
            succeeded, value = queue.get(timeout=self.timeout_seconds)
        except Empty as exc:
            raise ProviderTimeoutError("Tushare request timed out") from exc
        if succeeded:
            return value
        if isinstance(value, BaseException):
            raise value
        raise ProviderUnavailableError("Tushare request failed without an exception")

    def _safe_message(self, exc: Exception) -> str:
        message = str(exc).replace(self._token, "***") if self._token else str(exc)
        return message[:300]

    def _translate(self, exc: Exception) -> DataSourceError:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return ProviderTimeoutError(type(exc).__name__)
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {401}:
            return ProviderAuthenticationError("Tushare authentication failed")
        if status in {403}:
            return ProviderPermissionError("Tushare permission denied")
        if status == 429:
            return ProviderRateLimitError("Tushare rate limit reached")
        if isinstance(exc, (ConnectionError, OSError)):
            return ProviderUnavailableError(type(exc).__name__)
        message = self._safe_message(exc)
        lower = message.lower()
        if "权限" in message or "permission" in lower:
            return ProviderPermissionError(message)
        if "频率" in message or "rate limit" in lower:
            return ProviderRateLimitError(message)
        if "token" in lower or "认证" in message:
            return ProviderAuthenticationError(message)
        return ProviderUnavailableError(f"{type(exc).__name__}: {message}")
