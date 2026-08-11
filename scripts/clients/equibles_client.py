"""Equibles REST API client (off-exchange volume, short crowding, 13F, CFTC COT, filings).

Base URL   : https://api.equibles.com/v1  (all GET, Bearer auth)
Auth       : EQUIBLES_API_KEY (repo-root .env)
Allowance  : Pro plan 100,000 requests/day, shared with MCP, resets 00:00 UTC.
             Every paged request is billed separately - see ``paginate``.

Usage:
    from clients.equibles_client import EquiblesClient

    with EquiblesClient() as client:
        latest = client.get_cftc_latest_positions(category="Metals")
        walk = client.paginate(client.get_short_volume, "AAPL", limit=500, max_pages=3)
        print(walk.rows, walk.requests_used, client.rate_limit_remaining)

RATIO CONVENTION (the one thing every caller must know)
-------------------------------------------------------
The published docs claim every ratio field arrives as a fraction. That is FALSE.
Equibles MIXES conventions, and the same quantity is encoded differently by
different endpoints. Verified live 2026-08-11 (full table: docs/equibles-api.md):

    /short-squeeze-scores  shortInterestPercentOfShares: 0.0134   <- fraction
    /screener/stocks       shortInterestPercent:         1.34     <- percent
    /stocks/{t}/short-volume  shortVolumePercent:        39.37    <- percent

Confirmed by computing ``shortPosition / sharesOutstanding`` independently:
NVDA 0.013391 vs a reported 0.01339 (ratio 1.000), AAPL 0.009978 vs 0.01004
(ratio 1.006). So blanket-scaling every ``...Percent`` field would render
``shortVolumePercent`` as 3937%.

This client therefore normalizes ONLY the fields confirmed to be fractions:

    every field in ``FRACTION_FIELDS`` is multiplied by 100 and RENAMED to a
    ``...Pct`` key, and the original fraction key is dropped.

A ``...Pct`` value is always a percent in 0-100 and is safe to render directly.
Fields in ``PERCENT_FIELDS`` are ALREADY percents and pass through untouched
under their original names. Fields in ``PERCENTILE_FIELDS`` are 0-100 ranks, not
ratios, and are never scaled. Both sets exist so the distinction is asserted by
tests rather than remembered.

NEVER infer a unit from a field name. A name ending in ``Percent`` proves
nothing. Add a field here only after observing it on the wire.

Nulls stay null, non-ratio fields are untouched, and nested objects/arrays are
walked. Pass ``normalize_ratios=False`` for the raw payload.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests
from requests.exceptions import ConnectionError as ReqConnectionError, Timeout as ReqTimeout

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# Exception Hierarchy
# ══════════════════════════════════════════════════════════════════════


class EquiblesAPIError(Exception):
    """Base exception for all Equibles API errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        response_body: Optional[dict] = None,
    ):
        self.status_code = status_code
        self.code = code
        self.response_body = response_body
        super().__init__(message)


class EquiblesAuthError(EquiblesAPIError):
    """Missing or rejected API key (401)."""


class EquiblesRateLimitError(EquiblesAPIError):
    """Daily allowance exhausted or too many requests (429)."""


class EquiblesNotFoundError(EquiblesAPIError):
    """Unknown ticker, contract, or institution (404)."""


class EquiblesValidationError(EquiblesAPIError):
    """Invalid parameter (400)."""


class EquiblesServerError(EquiblesAPIError):
    """Server-side failure (500 / 503)."""


_DEFAULT_BASE_URL = "https://api.equibles.com/v1"
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_FACTOR = 1.0  # seconds; exponential 1s, 2s, 4s
_DEFAULT_MAX_PAGES = 5
_MAX_RETRY_AFTER = 60.0  # never sleep longer than this on a 429

_ERROR_CODE_EXCEPTIONS = {
    "invalid_parameter": EquiblesValidationError,
    "unauthorized": EquiblesAuthError,
    "not_found": EquiblesNotFoundError,
    "rate_limited": EquiblesRateLimitError,
    "internal_error": EquiblesServerError,
    "service_unavailable": EquiblesServerError,
}

_STATUS_EXCEPTIONS = {
    400: EquiblesValidationError,
    401: EquiblesAuthError,
    403: EquiblesAuthError,
    404: EquiblesNotFoundError,
    429: EquiblesRateLimitError,
}

_RETRYABLE_EXCEPTIONS = (EquiblesRateLimitError, EquiblesServerError)

# Fraction-valued fields (0-1), mapped to their unambiguous percent-valued name.
# Every entry was observed on the wire 2026-08-11. All five currently come from
# /short-squeeze-scores. Do not add a field here on the strength of its name.
FRACTION_FIELDS: Dict[str, str] = {
    "shortInterestPercentOfShares": "shortInterestPctOfShares",
    "shortInterestChangePercent": "shortInterestChangePct",
    "shortVolumeShareTrend": "shortVolumeShareTrendPct",
    "failsToDeliverPercentOfShares": "failsToDeliverPctOfShares",
    "priceAboveVwap": "priceAboveVwapPct",
}

# Already percents (0-100). These pass through UNTOUCHED under their original
# names. Listed so the "do not scale" contract is test-enforced, not folklore.
# Scaling any of these is a 100x rendering bug.
PERCENT_FIELDS: frozenset = frozenset({
    "shortVolumePercent",           # 39.37 for NVDA; scaling gives 3937%
    "percentOfTotal",               # institutional-holders, 11.65
    "percentOfPortfolio",           # institutions sector-allocation, 21.99
    "percentOfUniverse",            # 13f/most-held, 71.90
    "changePercent",                # institutional-ownership, 2.04
    "qoqChangePercent",             # super-investors, -4.03
    "top10ConcentrationPercent",    # institutions summary, 91.11
    "top25ConcentrationPercent",    # institutions summary, 99.99
    "qoQTurnoverPercent",           # institutions summary, 5.23
    "shortInterestPercent",         # screener, 1.34
    "dividendYieldPercent",
    "revenueGrowthYoYPercent",
    "grossMarginPercent",
})

# 0-100 RANKS, not ratios. Never scaled, never renamed.
PERCENTILE_FIELDS: frozenset = frozenset({
    "shortInterestPercentile",
    "daysToCoverPercentile",
    "shortVolumeTrendPercentile",
    "shortInterestChangePercentile",
})


@dataclass
class PagedResult:
    """Rows gathered by a bounded pagination walk, with billing accounting."""

    rows: List[dict] = field(default_factory=list)
    pages: int = 0
    requests_used: int = 0
    has_more: bool = False


def normalize_fractions(payload: Any) -> Any:
    """Rewrite every known fraction field to its ``...Pct`` percent form."""
    if isinstance(payload, list):
        return [normalize_fractions(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    normalized: Dict[str, Any] = {}
    for key, value in payload.items():
        percent_key = FRACTION_FIELDS.get(key)
        if percent_key is None:
            normalized[key] = normalize_fractions(value)
            continue
        normalized[percent_key] = _to_percent(value)
    return normalized


def _to_percent(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return value * 100


# ══════════════════════════════════════════════════════════════════════
# Client
# ══════════════════════════════════════════════════════════════════════


class EquiblesClient:
    """Typed Equibles REST client with bounded retry and billed-request accounting."""

    FRACTION_FIELDS = FRACTION_FIELDS

    # ── init / lifecycle ───────────────────────────────────────────

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
        max_pages: int = _DEFAULT_MAX_PAGES,
        normalize_ratios: bool = True,
    ):
        self._api_key = api_key or os.environ.get("EQUIBLES_API_KEY")
        if not self._api_key:
            raise EquiblesAuthError(
                "EQUIBLES_API_KEY is not set. Add it to the repo-root .env "
                "or export it: export EQUIBLES_API_KEY='your-api-key'"
            )

        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._max_pages = max_pages
        self._normalize_ratios = normalize_ratios

        self.request_count = 0
        self.rate_limit_limit: Optional[int] = None
        self.rate_limit_remaining: Optional[int] = None
        self.rate_limit_reset: Optional[int] = None

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
                "User-Agent": "radon/2.0",
            }
        )

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "EquiblesClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── internal request layer ─────────────────────────────────────

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> dict:
        """Authenticated GET with bounded retry, error mapping, and normalization."""
        url = f"{self._base_url}/{endpoint.lstrip('/')}"

        for attempt in range(1 + self._max_retries):
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
            except (ReqConnectionError, ReqTimeout) as exc:
                if attempt < self._max_retries:
                    time.sleep(self._backoff_delay(attempt))
                    continue
                raise EquiblesAPIError(
                    f"Connection failed after {attempt + 1} attempts: {exc}"
                ) from exc

            self.request_count += 1
            self._record_rate_limit(resp)

            if 200 <= resp.status_code < 300:
                return self._normalized(self._safe_json(resp))

            error = self._classify_error(resp)
            is_last_attempt = attempt >= self._max_retries
            if not isinstance(error, _RETRYABLE_EXCEPTIONS) or is_last_attempt:
                raise error

            time.sleep(self._retry_delay(resp, error, attempt))

        raise EquiblesAPIError(f"Retries exhausted for {url}")

    def _normalized(self, payload: Any) -> Any:
        return normalize_fractions(payload) if self._normalize_ratios else payload

    def _classify_error(self, resp: requests.Response) -> EquiblesAPIError:
        body = self._safe_json(resp)
        envelope = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = envelope.get("code")
        message = envelope.get("message") or resp.reason or f"HTTP {resp.status_code}"

        exception_type = _ERROR_CODE_EXCEPTIONS.get(code) or _STATUS_EXCEPTIONS.get(
            resp.status_code
        )
        if exception_type is None:
            exception_type = EquiblesServerError if resp.status_code >= 500 else EquiblesAPIError

        return exception_type(
            message, status_code=resp.status_code, code=code, response_body=body
        )

    def _record_rate_limit(self, resp: requests.Response) -> None:
        self.rate_limit_limit = _as_int(resp.headers.get("X-RateLimit-Limit"))
        self.rate_limit_remaining = _as_int(resp.headers.get("X-RateLimit-Remaining"))
        self.rate_limit_reset = _as_int(resp.headers.get("X-RateLimit-Reset"))

    def _backoff_delay(self, attempt: int) -> float:
        return self._backoff_factor * (2**attempt)

    def _retry_delay(
        self, resp: requests.Response, error: EquiblesAPIError, attempt: int
    ) -> float:
        if isinstance(error, EquiblesRateLimitError):
            retry_after = _as_float(resp.headers.get("Retry-After"))
            if retry_after is not None:
                return min(max(retry_after, 1.0), _MAX_RETRY_AFTER)
        return self._backoff_delay(attempt)

    @staticmethod
    def _safe_json(resp: requests.Response) -> dict:
        try:
            body = resp.json()
        except Exception:
            return {}
        return body if isinstance(body, dict) else {"data": body}

    @staticmethod
    def _build_params(**kwargs: Any) -> Dict[str, Any]:
        """Query params with every None-valued key dropped."""
        return {k: v for k, v in kwargs.items() if v is not None}

    # ── pagination ─────────────────────────────────────────────────

    def paginate(
        self,
        fetcher: Callable[..., dict],
        *args: Any,
        limit: int = 100,
        max_pages: Optional[int] = None,
        **kwargs: Any,
    ) -> PagedResult:
        """Walk ``meta.hasMore`` for a paged endpoint, bounded by ``max_pages``.

        Every page is a separately billed request; ``PagedResult.requests_used``
        reports the true count (retries included) so callers can log budget.
        """
        page_bound = max_pages or self._max_pages
        requests_before = self.request_count
        rows: List[dict] = []
        offset = 0
        pages = 0
        has_more = False

        while pages < page_bound:
            payload = fetcher(*args, limit=limit, offset=offset, **kwargs)
            pages += 1
            rows.extend(_extract_rows(payload))
            has_more = bool((payload.get("meta") or {}).get("hasMore"))
            if not has_more:
                break
            offset += limit

        return PagedResult(
            rows=rows,
            pages=pages,
            requests_used=self.request_count - requests_before,
            has_more=has_more,
        )

    # ══════════════════════════════════════════════════════════════════
    # F1 — ATS / OFF-EXCHANGE VENUE SHARE
    # ══════════════════════════════════════════════════════════════════

    def get_off_exchange_volume(
        self,
        ticker: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /stocks/{ticker}/off-exchange-volume - weekly ATS vs non-ATS OTC volume."""
        params = self._build_params(
            startDate=start_date, endDate=end_date, limit=limit, offset=offset
        )
        return self._get(f"stocks/{ticker.upper()}/off-exchange-volume", params=params)

    def get_short_volume(
        self,
        ticker: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /stocks/{ticker}/short-volume - daily FINRA short volume (shortVolumePct)."""
        params = self._build_params(
            startDate=start_date, endDate=end_date, limit=limit, offset=offset
        )
        return self._get(f"stocks/{ticker.upper()}/short-volume", params=params)

    def get_largest_short_volume(
        self,
        *,
        min_average_daily_volume: Optional[int] = None,
        min_market_cap: Optional[int] = None,
        min_dollar_volume: Optional[int] = None,
        sort_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """GET /short-volume/largest - market-wide largest short-volume names."""
        params = self._build_params(
            minAverageDailyVolume=min_average_daily_volume,
            minMarketCap=min_market_cap,
            minDollarVolume=min_dollar_volume,
            sortBy=sort_by,
            limit=limit,
        )
        return self._get("short-volume/largest", params=params)

    # ══════════════════════════════════════════════════════════════════
    # F2 — SHORT CROWDING / SQUEEZE
    # ══════════════════════════════════════════════════════════════════

    def get_short_interest(
        self,
        ticker: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /stocks/{ticker}/short-interest - bi-monthly settlement short position."""
        params = self._build_params(
            startDate=start_date, endDate=end_date, limit=limit, offset=offset
        )
        return self._get(f"stocks/{ticker.upper()}/short-interest", params=params)

    def get_short_interest_snapshot(
        self,
        *,
        min_average_daily_volume: Optional[int] = None,
        min_market_cap: Optional[int] = None,
        min_dollar_volume: Optional[int] = None,
        min_days_to_cover: Optional[float] = None,
        sort_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """GET /short-interest/snapshot - market-wide short-interest snapshot."""
        params = self._build_params(
            minAverageDailyVolume=min_average_daily_volume,
            minMarketCap=min_market_cap,
            minDollarVolume=min_dollar_volume,
            minDaysToCover=min_days_to_cover,
            sortBy=sort_by,
            limit=limit,
        )
        return self._get("short-interest/snapshot", params=params)

    def get_short_squeeze_scores(
        self,
        *,
        ticker: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /short-squeeze-scores - 0-100 squeeze score with factor percentiles."""
        params = self._build_params(
            ticker=ticker.upper() if ticker else None, limit=limit, offset=offset
        )
        return self._get("short-squeeze-scores", params=params)

    # ══════════════════════════════════════════════════════════════════
    # F3 — 13F SMART MONEY
    # ══════════════════════════════════════════════════════════════════

    def get_institutional_holders(
        self,
        ticker: str,
        *,
        report_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /stocks/{ticker}/institutional-holders - top holders by shares."""
        params = self._build_params(reportDate=report_date, limit=limit, offset=offset)
        return self._get(f"stocks/{ticker.upper()}/institutional-holders", params=params)

    def get_institutional_ownership(
        self, ticker: str, *, periods: Optional[int] = None
    ) -> dict:
        """GET /stocks/{ticker}/institutional-ownership - aggregate ownership by quarter."""
        params = self._build_params(periods=periods)
        return self._get(f"stocks/{ticker.upper()}/institutional-ownership", params=params)

    def get_institutional_activity(
        self,
        ticker: str,
        *,
        report_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """GET /stocks/{ticker}/institutional-activity - largest buyers and sellers QoQ."""
        params = self._build_params(reportDate=report_date, limit=limit)
        return self._get(f"stocks/{ticker.upper()}/institutional-activity", params=params)

    def get_13f_market_activity(
        self,
        *,
        bucket: Optional[str] = None,
        report_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """GET /13f/market-activity - top-buys | top-sells | new-positions | sold-out-positions."""
        params = self._build_params(bucket=bucket, reportDate=report_date, limit=limit)
        return self._get("13f/market-activity", params=params)

    def get_13f_most_held(
        self,
        *,
        sort: Optional[str] = None,
        report_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """GET /13f/most-held - most widely held names by filers | filersDelta | value."""
        params = self._build_params(sort=sort, reportDate=report_date, limit=limit)
        return self._get("13f/most-held", params=params)

    def search_institutions(self, query: str, *, limit: Optional[int] = None) -> dict:
        """GET /institutions - resolve an institution name to its CIK."""
        params = self._build_params(query=query, limit=limit)
        return self._get("institutions", params=params)

    def get_institution_portfolio(
        self,
        cik: str,
        *,
        report_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /institutions/{cik}/portfolio - full reported holdings."""
        params = self._build_params(reportDate=report_date, limit=limit, offset=offset)
        return self._get(f"institutions/{cik}/portfolio", params=params)

    def get_institution_summary(self, cik: str) -> dict:
        """GET /institutions/{cik}/summary - portfolio size and concentration."""
        return self._get(f"institutions/{cik}/summary")

    def get_institution_sector_allocation(
        self, cik: str, *, report_date: Optional[str] = None
    ) -> dict:
        """GET /institutions/{cik}/sector-allocation - weights by sector."""
        params = self._build_params(reportDate=report_date)
        return self._get(f"institutions/{cik}/sector-allocation", params=params)

    def get_institution_activity(
        self,
        cik: str,
        *,
        report_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /institutions/{cik}/activity - adds, trims, new and exited positions."""
        params = self._build_params(reportDate=report_date, limit=limit, offset=offset)
        return self._get(f"institutions/{cik}/activity", params=params)

    def get_super_investors(self) -> dict:
        """GET /super-investors - curated roster of renowned managers."""
        return self._get("super-investors")

    def get_consensus_holdings(
        self,
        institutions: Iterable[str] | str,
        *,
        report_date: Optional[str] = None,
        min_funds: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """GET /consensus-holdings - names held across 2-25 institutions."""
        params = self._build_params(
            institutions=_join_csv(institutions),
            reportDate=report_date,
            minFunds=min_funds,
            limit=limit,
        )
        return self._get("consensus-holdings", params=params)

    # ══════════════════════════════════════════════════════════════════
    # F4 — CFTC COT POSITIONING
    # ══════════════════════════════════════════════════════════════════

    def get_cftc_contracts(
        self, *, query: Optional[str] = None, alias: Optional[str] = None
    ) -> dict:
        """GET /cftc/contracts - contract roster; resolve an alias (GC, ES, WTI) to a code."""
        params = self._build_params(query=query, alias=alias)
        return self._get("cftc/contracts", params=params)

    def get_cftc_positions(
        self,
        market_code: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /cftc/contracts/{marketCode}/positions - weekly COT, newest first."""
        params = self._build_params(
            startDate=start_date, endDate=end_date, limit=limit, offset=offset
        )
        return self._get(f"cftc/contracts/{market_code}/positions", params=params)

    def get_cftc_latest_positions(self, *, category: Optional[str] = None) -> dict:
        """GET /cftc/positions/latest - latest report per contract, optionally by category."""
        params = self._build_params(category=category)
        return self._get("cftc/positions/latest", params=params)

    # ══════════════════════════════════════════════════════════════════
    # F5 — FILING FORENSICS
    # ══════════════════════════════════════════════════════════════════

    def get_proposed_sales(
        self,
        ticker: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /stocks/{ticker}/proposed-sales - Form 144 forward-looking insider supply."""
        params = self._build_params(
            startDate=start_date, endDate=end_date, limit=limit, offset=offset
        )
        return self._get(f"stocks/{ticker.upper()}/proposed-sales", params=params)

    def get_atm_programs(self, ticker: str) -> dict:
        """GET /stocks/{ticker}/atm-programs - at-the-market dilution overhang."""
        return self._get(f"stocks/{ticker.upper()}/atm-programs")

    def get_buyback_programs(self, ticker: str) -> dict:
        """GET /stocks/{ticker}/buyback-programs - authorized and remaining repurchase bid."""
        return self._get(f"stocks/{ticker.upper()}/buyback-programs")

    def get_executive_changes(
        self,
        ticker: str,
        *,
        action: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> dict:
        """GET /stocks/{ticker}/executive-changes - Appointed | Resigned | Terminated | Retired."""
        params = self._build_params(
            action=action,
            startDate=start_date,
            endDate=end_date,
            limit=limit,
            offset=offset,
        )
        return self._get(f"stocks/{ticker.upper()}/executive-changes", params=params)

    def get_stock_screener(self, **filters: Any) -> dict:
        """GET /screener/stocks - market-wide screen; filters pass through in camelCase."""
        return self._get("screener/stocks", params=self._build_params(**filters))


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def _extract_rows(payload: dict) -> List[dict]:
    rows = payload.get("data")
    if isinstance(rows, list):
        return rows
    return []


def _join_csv(values: Iterable[str] | str) -> str:
    if isinstance(values, str):
        return values
    return ",".join(str(v) for v in values)


def _as_int(raw: Optional[str]) -> Optional[int]:
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_float(raw: Optional[str]) -> Optional[float]:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
