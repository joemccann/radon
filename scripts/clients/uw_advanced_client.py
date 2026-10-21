"""Advanced UW research adapters; all HTTP goes through governed UWClient.

Verified against official /api/openapi on 2026-10-21. These are descriptive
research datasets, never broker quotes, positions, or execution instructions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from clients.uw_client import UWClient

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]{0,79}$")
_STANCES = {"sell_premium", "sell_vega", "directional", "leaps", "cheapies"}
_INDICATORS = {"gdp", "gdp-per-capita", "treasury-yield", "fed-funds", "cpi", "inflation", "retail-sales", "durables", "unemployment", "payrolls"}
_COMMODITIES = {"wti", "brent", "natural-gas", "copper", "aluminum", "wheat", "corn", "cotton", "sugar", "coffee", "all-commodities"}


def symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid research ticker")
    value = value.strip().upper()
    if not _SYMBOL.fullmatch(value):
        raise ValueError("Invalid research ticker")
    return value


def segment(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid research identifier")
    if not _SEGMENT.fullmatch(value):
        raise ValueError("Invalid research identifier")
    return value


def decimal_string(value: Any) -> str | None:
    """Canonical finite exact decimal without changing debit/credit signs."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
        return format(parsed, "f") if parsed.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def display_futures_price(raw_price: Any, display_factor: Any) -> str | None:
    """For RAW CME integer frames only; REST prices are already displayed."""
    raw, factor = decimal_string(raw_price), decimal_string(display_factor)
    if raw is None or factor is None or Decimal(factor) <= 0:
        return None
    return format(Decimal(raw) * Decimal(factor), "f")


class UWAdvancedClient(UWClient):
    def get_multi_leg(self, ticker: str, *, newer_than: datetime | None = None,
                      older_than: datetime | None = None, limit: int = 50,
                      offset: int = 0) -> dict:
        if not 1 <= limit <= 500 or not 0 <= offset <= 500:
            raise ValueError("Multi-leg page outside provider bounds")
        end = older_than or datetime.now(timezone.utc)
        start = newer_than or end - timedelta(hours=24)
        if end.tzinfo is None or start.tzinfo is None or end <= start or end - start > timedelta(hours=24):
            raise ValueError("Multi-leg queries require an ordered UTC range of at most 24 hours")
        return self._get("option-trades/multi-leg", {
            "ticker_symbol": symbol(ticker), "newer_than": start.astimezone(timezone.utc).isoformat(),
            "older_than": end.astimezone(timezone.utc).isoformat(), "limit": limit, "offset": offset,
        })

    def get_multi_leg_legs(self, strategy_id: str) -> dict:
        return self._get(f"option-trades/multi-leg/{segment(strategy_id)}/legs")

    def get_options_pulse(self, ticker: str, **params: Any) -> dict:
        return self._get(f"stock/{symbol(ticker)}/options-pulse", self._build_params(**params))

    def get_options_pulse_market(self, **params: Any) -> dict:
        return self._get("options-pulse/total", self._build_params(**params))

    def get_options_pulse_sectors(self, **params: Any) -> dict:
        return self._get("options-pulse/sectors", self._build_params(**params))

    def get_options_pulse_scanner(self, **params: Any) -> dict:
        return self._get("options-pulse/top", self._build_params(**{"limit": 50, **params}))

    def get_volatility_context(self, ticker: str, **params: Any) -> dict:
        return self._get(f"stock/{symbol(ticker)}/volatility/context", self._build_params(**params))

    def get_volatility_anomaly(self, ticker: str) -> dict:
        return self._get(f"stock/{symbol(ticker)}/volatility/anomaly")

    def get_volatility_character(self, ticker: str) -> dict:
        return self._get(f"stock/{symbol(ticker)}/volatility/character")

    def get_option_stance(self, ticker: str, stance: str = "directional") -> dict:
        if stance not in _STANCES:
            raise ValueError("Unsupported descriptive stance")
        return self._get(f"stock/{symbol(ticker)}/option-stance", {"stance": stance, "limit": 20})

    def get_spot_exposures_by_expiry_strike(self, ticker: str, *, limit: int = 100, page: int = 0) -> dict:
        if not 1 <= limit <= 500 or not 0 <= page <= 100:
            raise ValueError("Exposure page outside collection bounds")
        return self._get(f"stock/{symbol(ticker)}/spot-exposures/expiry-strike", {"limit": limit, "page": page})

    def get_company_profile(self, ticker: str) -> dict:
        return self._get(f"companies/{symbol(ticker)}/profile")

    def get_earnings_estimates(self, ticker: str) -> dict:
        return self._get(f"companies/{symbol(ticker)}/earnings-estimates")

    def get_company_dividends(self, ticker: str) -> dict:
        return self._get(f"companies/{symbol(ticker)}/dividends")

    def get_company_splits(self, ticker: str) -> dict:
        return self._get(f"companies/{symbol(ticker)}/splits")

    def get_company_transcript(self, ticker: str, quarter: str) -> dict:
        if not re.fullmatch(r"[0-9]{4}Q[1-4]", quarter):
            raise ValueError("Quarter must be YYYYQ1 through YYYYQ4")
        return self._get(f"companies/{symbol(ticker)}/transcripts/{quarter}")

    def get_listings(self, *, status: str = "active", date: str | None = None) -> dict:
        if status not in {"active", "delisted"}:
            raise ValueError("Listings status must be active or delisted")
        return self._get("companies/listings", self._build_params(status=status, date=date))

    def get_analytics(self, ticker: str, *, sliding: bool = False, range_: str = "2month") -> dict:
        if not re.fullmatch(r"[1-9][0-9]?(day|week|month|year)", range_):
            raise ValueError("Analytics requires a bounded relative window")
        return self._get("analytics/sliding" if sliding else "analytics/window", {
            "symbols": symbol(ticker), "range": range_, "interval": "DAILY",
            "calculations": "MEAN,STDDEV,MAX_DRAWDOWN", **({"window_size": 20} if sliding else {}),
        })

    def get_economic_indicator(self, indicator: str = "fed-funds") -> dict:
        if indicator not in _INDICATORS:
            raise ValueError("Unsupported economic series")
        return self._get(f"economy/{indicator}")

    def get_commodity_series(self, name: str, *, interval: str = "monthly") -> dict:
        if name not in _COMMODITIES or interval not in {"daily", "weekly", "monthly", "quarterly", "annual"}:
            raise ValueError("Unsupported commodity series")
        return self._get(f"commodities/{name}", {"interval": interval})

    def get_fx_history(self, from_currency: str, to_currency: str, *, interval: str = "daily") -> dict:
        pair = (from_currency.upper(), to_currency.upper())
        if not all(re.fullmatch(r"[A-Z]{3}", code) for code in pair) or interval not in {"daily", "weekly", "monthly"}:
            raise ValueError("Unsupported FX history request")
        return self._get("forex/history", {"from": pair[0], "to": pair[1], "interval": interval})

    def get_futures_contracts(self, *, query: str | None = None) -> dict:
        return self._get("futures/contracts", self._build_params(q=segment(query) if query else None, days=3))

    def get_futures_display_factor(self, security_id: int, *, contract: str | None = None) -> dict:
        if isinstance(security_id, bool) or not isinstance(security_id, int) or security_id <= 0:
            raise ValueError("CME security_id must be positive")
        return self._get(f"futures/display_factors/{security_id}", self._build_params(symbol=segment(contract) if contract else None))

    def get_futures_blocks(self, contract: str, *, limit: int = 50) -> dict:
        if not 1 <= limit <= 500:
            raise ValueError("Futures limit outside collection bounds")
        return self._get(f"futures/{segment(contract)}/trades", {"blocks_only": "true", "limit": limit})

    def get_futures_stats(self, contract: str) -> dict:
        return self._get(f"futures/{segment(contract)}/stats")

    def get_futures_candles(self, contract: str) -> dict:
        return self._get(f"futures/{segment(contract)}/candles", {"interval": "5m", "range": "1d"})
