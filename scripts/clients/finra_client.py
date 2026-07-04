"""FINRA margin-statistics client.

FINRA publishes monthly margin statistics (customer debit balances + free
credit balances, $ millions, Jan 1997+) as a single XLSX overwritten in place
roughly the third week of each month. There is no JSON API; the workbook IS
the primary source. Callers polling on a schedule should pass the previously
seen Last-Modified so an unchanged file costs one 304.

The site sits behind Cloudflare, which (verified 2026-07-03) serves this
static file to plain non-browser clients but answers BROWSER-like User-Agents
with a "Just a moment..." JS challenge. Keep the UA honest and non-browser;
do not "fix" a 403 by impersonating Chrome — that is what causes it.
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

import requests

_DEFAULT_XLSX_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_FACTOR = 1.0  # seconds; exponential backoff multiplier
_USER_AGENT = "radon/2.0"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class FinraClientError(Exception):
    """Base exception for FINRA download failures."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class FinraServerError(FinraClientError):
    """Retryable server-side / rate-limit failure that exhausted retries."""


class FinraClient:
    """Downloads the FINRA margin-statistics workbook with conditional GET."""

    def __init__(
        self,
        url: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
    ):
        self._url = url or os.environ.get("FINRA_MARGIN_XLSX_URL") or _DEFAULT_XLSX_URL
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT})

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "FinraClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fetch_margin_statistics(
        self, if_modified_since: Optional[str] = None
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """Return (xlsx bytes, Last-Modified header).

        With if_modified_since set, an unchanged file returns (None, same
        stamp) via HTTP 304 so scheduled polls no-op cheaply.
        """
        headers = {"If-Modified-Since": if_modified_since} if if_modified_since else {}
        last_exc: Optional[Exception] = None

        for attempt in range(1 + self._max_retries):
            try:
                resp = self._session.get(self._url, headers=headers, timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    time.sleep(self._backoff_factor * (2**attempt))
                    continue
                raise FinraClientError(
                    f"Connection failed after {attempt + 1} attempts: {exc}"
                ) from exc

            if resp.status_code == 304:
                return None, if_modified_since
            if resp.status_code == 200:
                return resp.content, resp.headers.get("Last-Modified")
            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                time.sleep(self._backoff_factor * (2**attempt))
                continue
            raise (
                FinraServerError if resp.status_code in _RETRYABLE_STATUS_CODES else FinraClientError
            )(f"HTTP {resp.status_code} fetching {self._url}", status_code=resp.status_code)

        raise FinraServerError(f"Retries exhausted fetching {self._url}: {last_exc}")
