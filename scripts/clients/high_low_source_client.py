"""Approved StockCharts common-stock breadth history, with bounded honest HTTP."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

HISTORY_URL = "https://stockcharts.com/quotebrain/historyandquote/d"
SYMBOLS = ("!NEWHINYC", "!NEWLONYC")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_RETRYABLE = {429, 500, 502, 503, 504}


class HighLowSourceError(RuntimeError):
    """Source unavailable; callers must preserve the previous snapshot."""


class HighLowSourceClient:
    def __init__(self, *, timeout=25, max_retries=2, backoff_factor=1.0, deadline=240):
        if timeout <= 0 or max_retries < 0 or deadline <= 0:
            raise ValueError("Invalid source request bounds")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.expires = time.monotonic() + deadline
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "radon/2.0"})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.session.close()

    def _remaining(self):
        remaining = self.expires - time.monotonic()
        if remaining <= 0:
            raise HighLowSourceError("StockCharts source deadline exceeded")
        return remaining

    def fetch_history(self, symbol, *, now=None):
        if symbol not in SYMBOLS:
            raise ValueError("Unsupported common-stock breadth symbol")
        now = now or datetime.now(timezone.utc)
        if now.utcoffset() is None:
            raise ValueError("now must be timezone aware")
        params = {"symbol": symbol, "start": "20030101",
                  "end": now.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")}
        for attempt in range(self.max_retries + 1):
            try:
                remaining = self._remaining()
                with self.session.get(HISTORY_URL, params=params, stream=True,
                                      allow_redirects=False,
                                      timeout=(min(8, remaining), min(self.timeout, remaining))) as response:
                    if response.status_code != 200:
                        if response.status_code not in _RETRYABLE or attempt == self.max_retries:
                            raise HighLowSourceError(f"StockCharts HTTP {response.status_code}")
                    else:
                        body = bytearray()
                        for chunk in response.iter_content(chunk_size=65536):
                            self._remaining()
                            body.extend(chunk)
                            if len(body) > MAX_RESPONSE_BYTES:
                                raise HighLowSourceError("StockCharts decoded body exceeds 8 MiB")
                        if not body:
                            raise HighLowSourceError("StockCharts returned an empty body")
                        return bytes(body)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise HighLowSourceError("StockCharts connection retries exhausted") from exc
            delay = self.backoff_factor * 2 ** attempt
            if delay >= self._remaining():
                raise HighLowSourceError("StockCharts retry exceeds source deadline")
            time.sleep(delay)
        raise HighLowSourceError("StockCharts retries exhausted")  # defensive
