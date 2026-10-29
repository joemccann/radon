#!/usr/bin/env python3
"""Bounded, provenance-preserving Unusual Whales research snapshots.

This enriches research only. It does not provide broker quotes, position marks,
trade approvals or orders. Missing/denied sections never become empty successes.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
from contextlib import nullcontext, contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from clients.uw_advanced_client import UWAdvancedClient, decimal_string, symbol
from clients.uw_client import UWAPIError, UWAuthError, UWRateLimitError
from utils.atomic_io import atomic_save, verified_load

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "uw_research"
DEFAULT_SECTIONS = ("multi_leg", "pulse", "volatility", "company", "seasonality")
# Seconds until the next collection is useful; payload timestamps remain separate.
SECTION_TTLS = {
    "multi_leg": 120, "pulse": 120, "pulse_market": 120, "pulse_sectors": 300,
    "pulse_scanner": 300, "volatility": 300, "volatility_anomaly": 900,
    "volatility_character": 900, "stance": 900, "spot_exposures": 120,
    "company": 86400, "estimates": 21600, "corporate_actions": 86400,
    "transcript": 86400, "seasonality": 86400, "analytics": 21600,
    "macro": 21600, "futures": 300,
}
SECTION_NAMES = tuple(SECTION_TTLS)
LIMITATIONS = [
    "Research context only; Interactive Brokers remains execution and portfolio authority.",
    "Vendor strategy classification and stance scores are descriptive, not probabilities or executable quotes.",
    "Daily cleared open interest is not replaced by live volume or Options Pulse sentiment.",
    "Collection time is not provider execution, publication or availability time; provider timestamps are retained in the payload.",
]
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVES_PER_TICKER = 64


class RequestBudgetExceeded(Exception):
    pass


class _BoundedClient:
    def __init__(self, client: UWAdvancedClient, maximum: int):
        self.client = client
        self.maximum = maximum
        self.calls = 0

    def __getattr__(self, name: str):
        method = getattr(self.client, name)
        def invoke(*args, **kwargs):
            if self.calls >= self.maximum:
                raise RequestBudgetExceeded()
            self.calls += 1
            result = method(*args, **kwargs)
            if not isinstance(result, dict) or "data" not in result:
                raise ValueError("Provider response must contain data")
            # A response accepted by the client must also be finite, bounded JSON.
            if len(json.dumps(result, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
                raise ValueError("Provider response exceeds research snapshot bound")
            return result
        return invoke


def _utc(now: datetime | None = None) -> datetime:
    result = now or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Collection timestamp must have a timezone")
    return result.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def _status(sections: dict) -> str:
    good = sum(v.get("state") in {"ok", "empty"} for v in sections.values())
    incomplete = any(v.get("data", {}).get("coverage", {}).get("truncated", False) or any(t.get("legs_state") == "unavailable" for t in v.get("data", {}).get("trades", [])) for v in sections.values() if isinstance(v.get("data"), dict))
    return "ok" if good == len(sections) and good and not incomplete else "partial" if good else "unavailable"


def read_research(ticker: str, *, data_dir: Path | str | None = None,
                  now: datetime | None = None) -> dict:
    ticker = symbol(ticker)
    clock = _utc(now)
    path = Path(data_dir or DATA_DIR) / f"{ticker}.json"
    try:
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError("Snapshot exceeds read bound")
        result = verified_load(str(path))
        if result.get("schema") != 1 or result.get("ticker") != ticker or not isinstance(result.get("sections"), dict) or any(not isinstance(v, dict) for v in result["sections"].values()):
            raise ValueError("Snapshot identity mismatch")
    except (OSError, ValueError, TypeError, AttributeError):
        return {"schema": 1, "ticker": ticker, "source": "unusual_whales", "status": "unavailable",
                "missing": True, "generated_at": None, "sections": {}, "limitations": LIMITATIONS}
    for section in result["sections"].values():
        expiry = _parse_time(section.get("expires_at"))
        if section.get("state") in {"ok", "empty"} and (expiry is None or expiry <= clock):
            section["state"] = "stale"
    result["status"] = _status(result["sections"])
    return result


def _rows(payload: dict) -> list[dict]:
    rows = payload.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Provider rows are malformed")
    return rows


def _multi_leg(ticker: str, client: _BoundedClient, clock: datetime) -> dict:
    start = clock - timedelta(hours=24)
    rows: list[dict] = []
    pages = 0
    complete = False
    # Explicit 24h range; provider offset is row count, not a page number.
    for page in range(2):
        batch = _rows(client.get_multi_leg(ticker, newer_than=start, older_than=clock, limit=50, offset=50 * page))
        pages += 1
        rows.extend(deepcopy(batch))
        if len(batch) < 50:
            complete = True
            break
    seen: set[str] = set()
    distinct = []
    enriched = 0
    for row in rows:
        identity = row.get("id")
        if isinstance(identity, str) and identity in seen:
            continue
        if isinstance(identity, str):
            seen.add(identity)
        net = decimal_string(row.get("net_price"))
        row["net_price"] = net
        row["debit_credit"] = "debit" if net is not None and Decimal(net) > 0 else "credit" if net is not None and Decimal(net) < 0 else "flat" if net is not None else "unavailable"
        # NBBO bid/ask aggressor side is NOT an observed held-position direction.
        row["position_direction_inferred"] = False
        if identity and enriched < 3:
            try:
                row["legs"] = _rows(client.get_multi_leg_legs(str(identity)))
                row["legs_state"] = "ok" if row["legs"] else "empty"
            except (UWAPIError, ValueError, RequestBudgetExceeded):
                row["legs_state"] = "unavailable"
            enriched += 1
        else:
            row["legs_state"] = "not_requested"
        distinct.append(row)
    return {"trades": distinct, "coverage": {"range_start": start.isoformat(), "range_end": clock.isoformat(),
            "pages": pages, "page_limit": 50, "complete": complete, "truncated": not complete,
            "legs_enriched": enriched}, "classification_basis": "provider reconstructed market prints, not IB positions"}


def _section_data(name: str, ticker: str, client: _BoundedClient, clock: datetime,
                  quarter: str | None, futures_contract: str | None, security_id: int | None) -> dict:
    if name == "multi_leg":
        return _multi_leg(ticker, client, clock)
    single = {
        "pulse": lambda: client.get_options_pulse(ticker),
        "pulse_market": client.get_options_pulse_market,
        "pulse_sectors": client.get_options_pulse_sectors,
        "pulse_scanner": client.get_options_pulse_scanner,
        "volatility": lambda: client.get_volatility_context(ticker),
        "volatility_anomaly": lambda: client.get_volatility_anomaly(ticker),
        "volatility_character": lambda: client.get_volatility_character(ticker),
        "stance": lambda: client.get_option_stance(ticker),
        "spot_exposures": lambda: client.get_spot_exposures_by_expiry_strike(ticker),
        "company": lambda: client.get_company_profile(ticker),
        "estimates": lambda: client.get_earnings_estimates(ticker),
        "macro": client.get_economic_indicator,
    }
    if name in single:
        response = single[name]()
        if name in {"spot_exposures", "pulse_scanner"}:
            rows = response.get("data")
            response = {**response, "coverage": {"truncated": isinstance(rows, list) and len(rows) >= (100 if name == "spot_exposures" else 50), "page_limit": 100 if name == "spot_exposures" else 50}}
        return response
    if name == "corporate_actions":
        return {"dividends": client.get_company_dividends(ticker), "splits": client.get_company_splits(ticker)}
    if name == "transcript":
        if not quarter:
            raise ValueError("A verified issuer fiscal quarter is required")
        return client.get_company_transcript(ticker, quarter)
    if name == "seasonality":
        return {"monthly": client.get_monthly_seasonality(ticker), "year_month": client.get_year_month_seasonality(ticker),
                "comparison_basis": "UW monthly history; not presumed equivalent to EquityClock population, lookback, adjustments or win-rate definition"}
    if name == "analytics":
        return {"fixed": client.get_analytics(ticker), "sliding": client.get_analytics(ticker, sliding=True),
                "point_in_time": "First observed now; historical recalculation is not point-in-time availability evidence"}
    if name == "futures":
        if not futures_contract or security_id is None:
            raise ValueError("A dated futures contract and verified CME security_id are required")
        factor = client.get_futures_display_factor(security_id, contract=futures_contract)
        definition = factor.get("data")
        if not isinstance(definition, dict) or definition.get("symbol") != futures_contract or definition.get("security_id") != security_id:
            raise ValueError("CME contract definition does not match the requested identity")
        value = decimal_string(definition.get("display_factor"))
        if value is None or Decimal(value) <= 0:
            raise ValueError("CME display factor is unavailable")
        return {"display_factor": factor, "blocks": client.get_futures_blocks(futures_contract),
                "stats": client.get_futures_stats(futures_contract),
                "price_basis": "REST prices already displayed; do not apply raw CME display factors again",
                "direction_basis": "No direction inferred from stale or missing block NBBO"}
    raise ValueError("Unknown research section")


def _is_empty(data: dict) -> bool:
    if "trades" in data:
        return not data["trades"]
    return "data" in data and data["data"] in (None, [], {})



def _request_identity(name: str, ticker: str, *, quarter: str | None,
                      futures_contract: str | None, security_id: int | None) -> dict:
    """Versioned dataset request identity for TTL reuse and retained failures.

    New parameters must enter this key before being exposed. Legacy snapshots
    without the exact key are unverified and are never reused or retained as
    observations for a new request.
    """
    params: dict[str, Any] = {}
    if name == "transcript":
        params = {"quarter": quarter}
    elif name == "futures":
        params = {"contract": futures_contract, "security_id": security_id, "blocks_only": True, "limit": 50}
    elif name == "analytics":
        params = {"range": "2month", "interval": "DAILY", "calculations": ["MEAN", "STDDEV", "MAX_DRAWDOWN"], "sliding_window_size": 20}
    elif name == "macro":
        params = {"indicator": "fed-funds"}
    elif name == "stance":
        params = {"stance": "directional", "limit": 20}
    elif name == "spot_exposures":
        params = {"limit": 100, "page": 0}
    elif name == "pulse_scanner":
        params = {"limit": 50}
    elif name == "multi_leg":
        # A rolling-window refresh is expected to advance; TTL bounds its reuse.
        params = {"window_hours": 24, "max_pages": 2, "page_limit": 50, "leg_limit": 3}
    return {"schema": 1, "ticker": ticker, "section": name, "params": params}


def _collect_research(ticker: str, *, sections: list[str] | tuple[str, ...] | None = None,
                     client: UWAdvancedClient | None = None, now: datetime | None = None,
                     persist: bool = True, data_dir: Path | str | None = None,
                     quarter: str | None = None, futures_contract: str | None = None,
                     security_id: int | None = None, max_requests: int = 12,
                     force: bool = False) -> dict:
    ticker = symbol(ticker)
    clock = _utc(now)
    selected = tuple(dict.fromkeys(DEFAULT_SECTIONS if sections is None else sections))
    if not selected or len(selected) > 6 or any(n not in SECTION_TTLS for n in selected):
        raise ValueError("Choose one to six supported research sections")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= 24:
        raise ValueError("Research request budget must be between 1 and 24")
    prior = read_research(ticker, data_dir=data_dir, now=clock)
    stored = deepcopy(prior.get("sections", {}))
    context = nullcontext(client) if client is not None else UWAdvancedClient(timeout=10, max_retries=0)
    with context as real:
        bounded = _BoundedClient(real, max_requests)
        for name in selected:
            previous = stored.get(name, {})
            # Parameter-sensitive sections cannot reuse another issuer quarter/contract.
            request_key = _request_identity(name, ticker, quarter=quarter,
                                            futures_contract=futures_contract, security_id=security_id)
            same_request = previous.get("request") == request_key
            if not force and same_request and previous.get("state") in {"ok", "empty"}:
                continue
            observed_at = clock.isoformat()
            try:
                data = _section_data(name, ticker, bounded, clock, quarter, futures_contract, security_id)
                # Never promote omitted data to an empty provider observation.
                if not isinstance(data, dict):
                    raise ValueError("Provider section is malformed")
                state = "empty" if _is_empty(data) else "ok"
                entry = {"state": state, "data": data, "source": "unusual_whales",
                         "observed_at": observed_at, "as_of": None,
                         "expires_at": (clock + timedelta(seconds=SECTION_TTLS[name])).isoformat(),
                         "request": request_key, "cadence_seconds": SECTION_TTLS[name]}
            except (UWAPIError, ValueError, RequestBudgetExceeded) as exc:
                if isinstance(exc, UWAuthError):
                    code = "entitlement_denied" if exc.status_code == 403 else "authentication_required"
                elif isinstance(exc, UWRateLimitError):
                    code = "rate_limited"
                elif isinstance(exc, RequestBudgetExceeded):
                    code = "request_budget_exhausted"
                elif isinstance(exc, ValueError):
                    code = "configuration_or_schema_invalid"
                else:
                    code = "upstream_unavailable"
                # Preserve the last observation with its original time and identity.
                entry = deepcopy(previous) if same_request and previous.get("data") is not None else {
                    "data": None, "source": "unusual_whales", "observed_at": None,
                    "as_of": None, "expires_at": None, "request": request_key,
                }
                entry.update(state="stale" if entry.get("data") is not None else "unavailable",
                             error_code=code, attempted_at=observed_at)
            stored[name] = entry
    result = {"schema": 1, "ticker": ticker, "source": "unusual_whales", "generated_at": clock.isoformat(),
              "status": _status(stored), "sections": stored, "limitations": LIMITATIONS,
              "request_count": bounded.calls, "request_count_basis": "bounded adapter invocations; provider usage is reconciled by the shared governor", "selected_sections": list(selected)}
    if persist:
        if len(json.dumps(result, allow_nan=False).encode()) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Research snapshot exceeds bound")
        target = Path(data_dir or DATA_DIR)
        if bounded.calls:
            _archive_snapshot(target, ticker, result)
        atomic_save(str(target / f"{ticker}.json"), result)
    return result



@contextmanager
def _snapshot_lock(directory: Path, ticker: str):
    """Serialize read/refresh/write so an older refresh cannot erase newer state."""
    directory.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(directory / f".{ticker}.lock", flags, 0o600)
    try:
        deadline = time.monotonic() + 1.0
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError("Research refresh already in progress") from None
                time.sleep(0.02)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _archive_snapshot(directory: Path, ticker: str, result: dict) -> None:
    """Keep bounded first-observed snapshots, not retroactive availability claims."""
    encoded = json.dumps(result, sort_keys=True, allow_nan=False)
    identity = hashlib.sha256(encoded.encode()).hexdigest()[:16]
    stamp = result["generated_at"].replace(":", "-").replace("+", "_")
    history = directory / "history" / ticker
    atomic_save(str(history / f"{stamp}-{identity}.json"), result)
    # Archive size is a deliberate research-retention bound, not promised history.
    per_ticker = sorted(history.glob("*.json"), key=lambda p: p.name)
    for old in per_ticker[:-MAX_ARCHIVES_PER_TICKER]:
        old.unlink(missing_ok=True)
    all_archives = []
    for path in (directory / "history").glob("*/*.json"):
        try:
            all_archives.append((path.stat().st_mtime_ns, path.stat().st_size, path))
        except FileNotFoundError:
            continue
    total = sum(size for _, size, _ in all_archives)
    for _, size, old in sorted(all_archives):
        if total <= MAX_ARCHIVE_BYTES:
            break
        old.unlink(missing_ok=True)
        total -= size


def collect_research(ticker: str, *, sections: list[str] | tuple[str, ...] | None = None,
                     client: UWAdvancedClient | None = None, now: datetime | None = None,
                     persist: bool = True, data_dir: Path | str | None = None,
                     quarter: str | None = None, futures_contract: str | None = None,
                     security_id: int | None = None, max_requests: int = 12,
                     force: bool = False) -> dict:
    ticker = symbol(ticker)
    kwargs = dict(sections=sections, client=client, now=now, persist=persist,
                  data_dir=data_dir, quarter=quarter, futures_contract=futures_contract,
                  security_id=security_id, max_requests=max_requests, force=force)
    if not persist:
        return _collect_research(ticker, **kwargs)
    with _snapshot_lock(Path(data_dir or DATA_DIR), ticker):
        return _collect_research(ticker, **kwargs)



def _record_health(status: str) -> None:
    """Best effort only: a telemetry write cannot erase a completed snapshot."""
    try:
        from db.writer import record_service_health
        record_service_health("uw-research", "ok" if status == "ok" else "error",
                              finished_at=datetime.now(timezone.utc).isoformat(),
                              error=None if status == "ok" else {"message": "Research datasets incomplete; retained observations remain available"})
    except Exception:
        print("[uw-research] health telemetry unavailable", file=sys.stderr)


def _rate_limit_only(result: dict) -> bool:
    """True when every section of a finished cycle failed only as a 429.

    The snapshot already kept the last matching observation. The next timer
    retries. Auth, schema, budget and other upstream failures stay fatal.
    """
    sections = result.get("sections")
    if result.get("status") != "unavailable" or not isinstance(sections, dict) or not sections:
        return False
    for section in sections.values():
        if not isinstance(section, dict) or section.get("state") in {"ok", "empty"}:
            return False
        if section.get("error_code") != "rate_limited":
            return False
    return True


def _scheduled_cycle_ok(result: dict) -> bool:
    return result.get("status") in {"ok", "partial"} or _rate_limit_only(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticker")
    group.add_argument("--tickers")
    group.add_argument("--scheduled", action="store_true")
    parser.add_argument("--sections", help="Comma-separated section names (maximum six)")
    parser.add_argument("--quarter")
    parser.add_argument("--futures-contract")
    parser.add_argument("--security-id", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON output (always used)")
    args = parser.parse_args(argv)
    try:
        from uw_stream.config import hydrate_settings
        hydrate_settings()
        tickers = [symbol(v) for v in (args.ticker or args.tickers or os.environ.get("RADON_UW_RESEARCH_TICKERS", "SPY,QQQ")).split(",")]
        tickers = list(dict.fromkeys(tickers))
        if len(tickers) > 10:
            raise ValueError("Scheduled research universe is limited to ten tickers")
        sections = args.sections.split(",") if args.sections else None
        results = [collect_research(ticker, sections=sections, quarter=args.quarter,
                   futures_contract=args.futures_contract, security_id=args.security_id, force=args.force) for ticker in tickers]
        payload = results[0] if len(results) == 1 else {"status": "ok" if all(r["status"] == "ok" for r in results) else "partial", "results": results}
        if args.scheduled:
            _record_health("ok" if all(r["status"] == "ok" for r in results) else "partial")
        print(json.dumps(payload, allow_nan=False))
        return 0 if all(_scheduled_cycle_ok(r) for r in results) else 1
    except Exception:
        if args.scheduled:
            _record_health("unavailable")
        # Never print upstream error bodies, URLs, tokens or raw exception strings.
        print(json.dumps({"status": "unavailable", "error_code": "research_collection_unavailable"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
