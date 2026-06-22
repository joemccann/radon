"""Public Polymarket REST client (Gamma + CLOB).

Read-only access to Polymarket's public market data — no authentication, no
API key. Two host surfaces are used:

  - Gamma  (https://gamma-api.polymarket.com)  -> markets metadata + questions
  - CLOB   (https://clob.polymarket.com)        -> live midpoint + price history

Usage:
    from clients.polymarket_client import PolymarketClient

    with PolymarketClient() as pm:
        markets = pm.get_markets(active=True, limit=200)
        mid = pm.get_midpoint(token_id)          # 0..1 binary midpoint
        hist = pm.get_price_history(token_id)

The client mirrors UWClient's shape: a connection-pooled ``requests.Session``,
exponential-backoff retry for transient 5xx/429, and a small exception
hierarchy mapped to HTTP status codes. Every endpoint returns parsed JSON
(list or dict) — convenience accessors unwrap the common shapes.

API surface verified against Polymarket public docs (Gamma markets, CLOB
``/midpoint`` and ``/prices-history``).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from requests.exceptions import ConnectionError as ReqConnectionError, Timeout as ReqTimeout

logger = logging.getLogger(__name__)


# ── exception hierarchy ────────────────────────────────────────────

class PolymarketAPIError(Exception):
    """Base exception for all Polymarket API errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[Any] = None,
    ):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class PolymarketNotFoundError(PolymarketAPIError):
    """Resource not found (404)."""


class PolymarketServerError(PolymarketAPIError):
    """Server-side error (5xx)."""


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_DEFAULT_GAMMA_URL = "https://gamma-api.polymarket.com"
_DEFAULT_CLOB_URL = "https://clob.polymarket.com"
_DEFAULT_TIMEOUT = 20
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_BACKOFF_FACTOR = 1.0


# ── pure conversions ───────────────────────────────────────────────

def midpoint_to_probability(midpoint: Any) -> Optional[float]:
    """Convert a Polymarket binary-market midpoint to an implied probability.

    For a binary YES/NO market the YES-token midpoint price IS the market's
    implied probability of YES (a $1 payout contract trading at $0.42 implies a
    42% chance). The value is clamped to [0, 1]; non-numeric input yields None.
    """
    if midpoint is None:
        return None
    try:
        value = float(midpoint)
    except (ValueError, TypeError):
        return None
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


# ── client ─────────────────────────────────────────────────────────

class PolymarketClient:
    """Public Polymarket REST client (Gamma metadata + CLOB pricing)."""

    def __init__(
        self,
        *,
        gamma_url: str = _DEFAULT_GAMMA_URL,
        clob_url: str = _DEFAULT_CLOB_URL,
        timeout: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
    ):
        self._gamma_url = gamma_url.rstrip("/")
        self._clob_url = clob_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor

        self._session = requests.Session()
        self._session.headers.update(
            {"Accept": "application/json", "User-Agent": "radon/2.0"}
        )

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "PolymarketClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── internal request layer ─────────────────────────────────────

    def _get(self, url: str, params: Optional[dict] = None) -> Any:
        """Authenticated-free GET with retry + error mapping. Returns parsed JSON."""
        last_exc: Optional[Exception] = None

        for attempt in range(1 + self._max_retries):
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
            except (ReqConnectionError, ReqTimeout) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise PolymarketAPIError(
                    f"Connection failed after {attempt + 1} attempts: {exc}"
                ) from exc

            status = resp.status_code
            if status == 200:
                return self._safe_json(resp)

            body = self._safe_json(resp)
            msg = self._error_message(body, resp, status)

            if status == 404:
                raise PolymarketNotFoundError(msg, status_code=status, response_body=body)
            if status in _RETRYABLE_STATUS_CODES:
                exc = PolymarketServerError(msg, status_code=status, response_body=body)
                last_exc = exc
                if attempt < self._max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise exc
            raise PolymarketAPIError(msg, status_code=status, response_body=body)

        raise last_exc  # type: ignore[misc]

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(self._backoff_factor * (2 ** attempt))

    @staticmethod
    def _safe_json(resp: requests.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return {}

    @staticmethod
    def _error_message(body: Any, resp: requests.Response, status: int) -> str:
        if isinstance(body, dict):
            return body.get("error") or body.get("message") or resp.reason or f"HTTP {status}"
        return resp.reason or f"HTTP {status}"

    @staticmethod
    def _as_list(payload: Any) -> list:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return data
        return []

    # ── endpoints ───────────────────────────────────────────────────

    def get_markets(
        self,
        *,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list:
        """GET {gamma}/markets — list markets metadata (questions, token ids)."""
        params = {
            k: v
            for k, v in {
                "active": active,
                "closed": closed,
                "limit": limit,
                "offset": offset,
            }.items()
            if v is not None
        }
        payload = self._get(f"{self._gamma_url}/markets", params=params)
        return self._as_list(payload)

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """GET {clob}/midpoint — midpoint price (0..1) for a CLOB token.

        Returns None when the response carries no ``mid`` field (e.g. an
        illiquid market with no two-sided book).
        """
        if not token_id:
            raise ValueError("token_id is required")
        payload = self._get(f"{self._clob_url}/midpoint", params={"token_id": token_id})
        if isinstance(payload, dict) and payload.get("mid") is not None:
            try:
                return float(payload["mid"])
            except (ValueError, TypeError):
                return None
        return None

    def get_price_history(
        self,
        token_id: str,
        *,
        interval: str = "1d",
        fidelity: Optional[int] = None,
    ) -> list:
        """GET {clob}/prices-history — time series of midpoint for a token."""
        if not token_id:
            raise ValueError("token_id is required")
        params = {"market": token_id, "interval": interval}
        if fidelity is not None:
            params["fidelity"] = fidelity
        payload = self._get(f"{self._clob_url}/prices-history", params=params)
        if isinstance(payload, dict) and isinstance(payload.get("history"), list):
            return payload["history"]
        return self._as_list(payload)
