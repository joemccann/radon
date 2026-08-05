"""Cboe US-indices daily-prices client.

Cboe serves per-index daily-price history as static CSVs on its public CDN
(the same files behind the Cboe dashboards), overwritten in place after each
session. There is no auth; the CSV IS the primary source. Callers polling on
a schedule should pass the previously seen Last-Modified so an unchanged
file costs one 304. Note Cboe re-touches Last-Modified intraday WITHOUT
appending the session row — a 304 means "bytes unchanged", not "no new
session pending".

Keep the UA honest and non-browser (never impersonate Chrome —
feedback_finra_cloudflare_blocks_browser_ua precedent applies to CDN edges
generally).
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

import requests

_DEFAULT_BASE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices"
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_FACTOR = 1.0  # seconds; exponential backoff multiplier
_USER_AGENT = "radon/2.0"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class CboeClientError(Exception):
    """Base exception for Cboe download failures."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class CboeServerError(CboeClientError):
    """Retryable server-side / rate-limit failure that exhausted retries."""


class CboeClient:
    """Downloads Cboe index daily-price history CSVs with conditional GET."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
    ):
        self._base_url = (
            base_url or os.environ.get("CBOE_DAILY_PRICES_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT})

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "CboeClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _history_url(self, symbol: str) -> str:
        return f"{self._base_url}/{symbol}_History.csv"

    def fetch_history(
        self, symbol: str, if_modified_since: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return (CSV text, Last-Modified header) for ``symbol`` ("SPX", "VIX1D").

        With if_modified_since set, an unchanged file returns (None, same
        stamp) via HTTP 304 so scheduled polls no-op cheaply.
        """
        url = self._history_url(symbol)
        headers = {"If-Modified-Since": if_modified_since} if if_modified_since else {}
        last_exc: Optional[Exception] = None

        for attempt in range(1 + self._max_retries):
            try:
                resp = self._session.get(url, headers=headers, timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    time.sleep(self._backoff_factor * (2**attempt))
                    continue
                raise CboeClientError(
                    f"Connection failed after {attempt + 1} attempts: {exc}"
                ) from exc

            if resp.status_code == 304:
                return None, if_modified_since
            if resp.status_code == 200:
                return resp.text, resp.headers.get("Last-Modified")
            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                time.sleep(self._backoff_factor * (2**attempt))
                continue
            raise (
                CboeServerError if resp.status_code in _RETRYABLE_STATUS_CODES else CboeClientError
            )(f"HTTP {resp.status_code} fetching {url}", status_code=resp.status_code)

        raise CboeServerError(f"Retries exhausted fetching {url}: {last_exc}")
