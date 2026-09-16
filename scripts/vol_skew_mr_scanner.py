#!/usr/bin/env python3
"""Vol/skew mean-reversion scanner (Options Insight / Imran Lakha framing).

Short-term top/bottom: technical extension (RSI and/or Bollinger %B), then
whether IV and skew confirmed the move or diverged from spot.

    Extended high + falling/flat IV -> TOP_MR (put spread when skew diverges)
    Rally + rising IV               -> BREAKOUT
    Extended low + falling/flat IV  -> BOTTOM_MR (call spread when skew diverges)
    Selloff + rising IV             -> BREAKDOWN

Credit: Options Insight / Imran Lakha. Strategy inspiration only.
Writes ``data/vol_skew_mr.json`` for the web UI.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from clients.uw_client import UWClient, UWRateLimitError
from strength_confirmation_scanner import resolve_tickers
from utils.scan_coverage import (
    coverage_block,
    is_coverage_failed,
    payload_has_candidates,
    should_persist_scan,
)
from utils.scan_health import (
    SCAN_STATUS_BUDGET_BLOCKED,
    SCAN_STATUS_COVERAGE_FAILED,
    next_quota_reset_iso,
    record_scan_degraded,
)
from utils.uw_budget import should_block_universe_scan
from utils.uw_surface import fetch_surface, scan_ib_session

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover - DB layer optional in tests/dev
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
_CACHE_PATH = _PROJECT_DIR / "data" / "vol_skew_mr.json"

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
RSI_HIGH = 70.0
RSI_LOW = 30.0
PATH_FLAT_EPS = 0.25
DEFAULT_MAX_WORKERS = 24
RATE_LIMIT_ABORT = 8
SERVICE_NAME = "vol-skew-mr"

_VERDICT_RANK = {
    "TOP_MR": 0,
    "BOTTOM_MR": 0,
    "BREAKOUT": 1,
    "BREAKDOWN": 1,
    "NO_SIGNAL": 2,
}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        parsed = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@dataclass
class GateResult:
    verdict: str
    suggested_structure: Optional[str]
    gates: Dict[str, bool]
    extension: str
    iv_path: str
    skew_path: str
    rsi: Optional[float] = None
    pct_b: Optional[float] = None


@dataclass
class VolSkewCandidate:
    ticker: str
    verdict: str
    spot: float
    rsi: Optional[float]
    pct_b: Optional[float]
    extension: str
    iv_path: str
    skew_path: str
    suggested_structure: Optional[str]
    gates: Dict[str, bool]
    errors: List[str]


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _as_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("data", [])
    else:
        rows = payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _vol_pct(value: Any) -> Optional[float]:
    num = _to_float(value)
    if num is None or num <= 0:
        return None
    return num * 100 if num <= 3 else num


def _prices_from_ohlc(payload: Any) -> List[float]:
    prices: List[float] = []
    for row in _as_rows(payload):
        close = _to_float(row.get("close"))
        if close is not None and close > 0:
            prices.append(close)
    return prices


def rsi(prices: Sequence[float], period: int = RSI_PERIOD) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for idx in range(len(prices) - period, len(prices)):
        change = prices[idx] - prices[idx - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def bollinger_pct_b(
    prices: Sequence[float],
    period: int = BB_PERIOD,
    num_std: float = BB_STD,
) -> Optional[float]:
    if len(prices) < period:
        return None
    window = list(prices)[-period:]
    mean = sum(window) / period
    variance = sum((price - mean) ** 2 for price in window) / period
    std = math.sqrt(variance)
    if std == 0:
        return 0.5
    upper = mean + num_std * std
    lower = mean - num_std * std
    width = upper - lower
    if width == 0:
        return 0.5
    return (window[-1] - lower) / width


def spot_extension(rsi: Optional[float], pct_b: Optional[float]) -> str:
    high = (rsi is not None and rsi >= RSI_HIGH) or (pct_b is not None and pct_b >= 1.0)
    low = (rsi is not None and rsi <= RSI_LOW) or (pct_b is not None and pct_b <= 0.0)
    if high and not low:
        return "HIGH"
    if low and not high:
        return "LOW"
    return "MID"


def series_path(values: Sequence[float], flat_eps: float = PATH_FLAT_EPS) -> str:
    if len(values) < 2:
        return "unknown"
    change = values[-1] - values[0]
    if change > flat_eps:
        return "rising"
    if change < -flat_eps:
        return "falling"
    return "flat"


def classify_gates(
    *,
    extension: str,
    iv_path: str,
    skew_path: str,
) -> GateResult:
    technicals = extension in {"HIGH", "LOW"}
    if not technicals:
        return GateResult(
            verdict="NO_SIGNAL",
            suggested_structure=None,
            gates={"technicals": False, "iv": False, "skew": False},
            extension=extension,
            iv_path=iv_path,
            skew_path=skew_path,
        )

    if extension == "HIGH":
        if iv_path in {"falling", "flat"}:
            verdict = "TOP_MR"
        elif iv_path == "rising":
            verdict = "BREAKOUT"
        else:
            verdict = "NO_SIGNAL"
    else:
        if iv_path in {"falling", "flat"}:
            verdict = "BOTTOM_MR"
        elif iv_path == "rising":
            verdict = "BREAKDOWN"
        else:
            verdict = "NO_SIGNAL"

    iv_ok = verdict != "NO_SIGNAL"
    skew_diverges = skew_path in {"falling", "flat"}
    structure: Optional[str] = None
    if verdict == "TOP_MR" and skew_diverges:
        structure = "put spread"
    elif verdict == "BOTTOM_MR" and skew_diverges:
        structure = "call spread"

    return GateResult(
        verdict=verdict,
        suggested_structure=structure,
        gates={"technicals": True, "iv": iv_ok, "skew": structure is not None},
        extension=extension,
        iv_path=iv_path,
        skew_path=skew_path,
    )


def classify_from_series(
    prices: Sequence[float],
    iv_series: Sequence[float],
    skew_series: Sequence[float],
) -> GateResult:
    rsi_value = rsi(prices)
    pct_b = bollinger_pct_b(prices)
    result = classify_gates(
        extension=spot_extension(rsi_value, pct_b),
        iv_path=series_path(iv_series),
        skew_path=series_path(skew_series),
    )
    result.rsi = None if rsi_value is None else round(rsi_value, 2)
    result.pct_b = None if pct_b is None else round(pct_b, 4)
    return result


def _dated_series(payload: Any, keys: Sequence[str]) -> List[float]:
    rows = sorted(_as_rows(payload), key=lambda row: str(row.get("date") or row.get("timestamp") or ""))
    values: List[float] = []
    for row in rows:
        value = None
        for key in keys:
            if key in {"volatility", "iv", "atm_iv"}:
                value = _vol_pct(row.get(key))
            else:
                value = _to_float(row.get(key))
            if value is not None:
                break
        if value is not None:
            values.append(value)
    return values[-6:]


def _parse_option_symbol(symbol: str) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    text = str(symbol or "").strip().upper()
    for idx in range(1, len(text) - 7):
        if text[idx:idx + 6].isdigit() and text[idx + 6:idx + 7] in {"C", "P"}:
            right = text[idx + 6]
            try:
                return f"20{text[idx:idx + 2]}{text[idx + 2:idx + 4]}{text[idx + 4:idx + 6]}", int(text[idx + 7:]) / 1000, right
            except ValueError:
                return None, None, right
    return None, None, None


def _current_put_call_skew(contracts_payload: Any) -> Optional[float]:
    options: List[Dict[str, Any]] = []
    for row in _as_rows(contracts_payload):
        symbol = str(row.get("option_symbol") or row.get("symbol") or row.get("id") or "")
        parsed_expiry, parsed_strike, parsed_right = _parse_option_symbol(symbol)
        expiry = str(row.get("expiry") or row.get("expiration") or parsed_expiry or "")
        strike = _to_float(row.get("strike") or row.get("strike_price")) or parsed_strike
        right = str(row.get("right") or row.get("option_type") or parsed_right or "").upper()[:1]
        iv = _vol_pct(row.get("implied_volatility") or row.get("iv") or row.get("volatility"))
        delta = _to_float(row.get("delta"))
        if not expiry or strike is None or right not in {"C", "P"} or iv is None:
            continue
        if delta is not None and right == "P" and delta > 0:
            delta = delta - 1
        options.append({"expiry": expiry, "right": right, "iv": iv, "delta": delta})
    if not options:
        return None
    target = sorted({row["expiry"] for row in options})[0]
    expiry_rows = [row for row in options if row["expiry"] == target]
    calls = [row for row in expiry_rows if row["right"] == "C"]
    puts = [row for row in expiry_rows if row["right"] == "P"]
    call25 = min(calls, key=lambda row: abs((row.get("delta") or 0.25) - 0.25), default=None)
    put25 = min(puts, key=lambda row: abs(abs(row.get("delta") or -0.25) - 0.25), default=None)
    if call25 is None or put25 is None:
        return None
    return put25["iv"] - call25["iv"]


def scan_ticker(
    ticker: str,
    client: Optional[Any] = None,
    *,
    retry_transient: bool = False,
    ib: Any = None,
) -> Optional[VolSkewCandidate]:
    own_client = None
    if client is None:
        client_kwargs = {} if retry_transient else {"max_retries": 0, "backoff_factor": 0}
        own_client = UWClient(**client_kwargs)
        client = own_client
    errors: List[str] = []
    ticker = ticker.upper()
    try:
        try:
            surface = fetch_surface(client, ticker, ib=ib)
            prices = _prices_from_ohlc(surface["ohlc"])
        except UWRateLimitError:
            raise
        except Exception as exc:
            errors.append(f"ohlc:{exc}")
            return None
        if len(prices) < BB_PERIOD:
            errors.append("insufficient_price_history")
            return None

        def fetch(label: str, func, default: Any) -> Any:
            try:
                return func()
            except UWRateLimitError:
                raise
            except Exception as exc:  # noqa: BLE001 - scanner degrades by gate
                errors.append(f"{label}:{exc}")
                return default

        empty = {"data": []}
        iv_series = _dated_series(fetch("iv_rank", lambda: surface["iv_rank"], empty), ("volatility", "iv"))
        rr = fetch(
            "risk_reversal",
            lambda: client.get_historical_risk_reversal_skew(ticker),
            empty,
        )
        skew_series = _dated_series(rr, ("value", "skew", "risk_reversal"))
        if len(skew_series) < 2:
            current = _current_put_call_skew(fetch("contracts", lambda: surface["contracts"], empty))
            if current is not None:
                skew_series = skew_series + [current] if skew_series else [current]

        classified = classify_from_series(prices, iv_series, skew_series)
        return VolSkewCandidate(
            ticker=ticker,
            verdict=classified.verdict,
            spot=round(prices[-1], 4),
            rsi=classified.rsi,
            pct_b=classified.pct_b,
            extension=classified.extension,
            iv_path=classified.iv_path,
            skew_path=classified.skew_path,
            suggested_structure=classified.suggested_structure,
            gates=classified.gates,
            errors=errors,
        )
    except UWRateLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 - per-ticker scanner isolation
        print(f"  {ticker} - ERROR ({exc})", file=sys.stderr)
        return None
    finally:
        if own_client is not None:
            own_client.close()


def build_output(
    results: List[VolSkewCandidate],
    source: str,
    universe_count: int,
    requested_tickers: Optional[Sequence[str]] = None,
    coverage: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    sorted_results = sorted(
        results,
        key=lambda row: (_VERDICT_RANK.get(row.verdict, 9), row.ticker),
    )
    actionable = sum(1 for row in sorted_results if row.verdict in {"TOP_MR", "BOTTOM_MR"})
    return {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "source": "Unusual Whales + Radon vol/skew feeds",
        "universe": source,
        "requested_tickers": list(requested_tickers or []),
        "tickers_scanned": universe_count,
        "coverage": coverage or coverage_block(
            tickers=universe_count, ok=len(sorted_results),
            no_setup=0, rate_limited=0, errors=0,
        ),
        "candidates_found": len(sorted_results),
        "actionable_count": actionable,
        "results": [asdict(row) for row in sorted_results],
    }


def scan_universe(
    tickers: Sequence[str],
    preset: Optional[str] = "ndx100",
    limit: Optional[int] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    retry_transient: bool = False,
) -> Dict[str, Any]:
    resolved, source = resolve_tickers(tickers, preset)
    if limit is not None and limit > 0:
        resolved = resolved[:limit]
    if not tickers and should_block_universe_scan():
        print(
            f"UW daily budget block; skipping universe scan ({source})",
            file=sys.stderr,
        )
        record_scan_degraded(
            SERVICE_NAME,
            SCAN_STATUS_BUDGET_BLOCKED,
            f"UW daily budget block; universe scan skipped ({source})",
            next_attempt_at=next_quota_reset_iso(),
        )
        prior = _read_cache_file(_CACHE_PATH)
        if payload_has_candidates(prior):
            return {**prior, "scan_status": SCAN_STATUS_BUDGET_BLOCKED}
        blocked = build_output(
            [], source, len(resolved), requested_tickers=resolved,
            coverage=coverage_block(
                tickers=len(resolved), ok=0, no_setup=0, rate_limited=0, errors=0,
            ),
        )
        blocked["scan_status"] = SCAN_STATUS_BUDGET_BLOCKED
        return blocked
    print(f"Scanning {len(resolved)} tickers for vol/skew mean-reversion ({source})...", file=sys.stderr)

    results: List[VolSkewCandidate] = []
    ok = 0
    no_setup = 0
    rate_limited = 0
    errors = 0
    consecutive_rate_limits = 0
    with scan_ib_session() as ib:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = {
                pool.submit(
                    scan_ticker,
                    ticker,
                    None,
                    retry_transient=retry_transient,
                    ib=ib,
                ): ticker
                for ticker in resolved
            }
            for idx, future in enumerate(as_completed(futures), start=1):
                ticker = futures[future]
                try:
                    row = future.result()
                except CancelledError:
                    continue
                except UWRateLimitError:
                    rate_limited += 1
                    consecutive_rate_limits += 1
                    print(f"  [{idx}/{len(resolved)}] {ticker} - SKIP (rate limited)", file=sys.stderr)
                    if consecutive_rate_limits >= RATE_LIMIT_ABORT:
                        print(
                            f"  aborting remaining tickers after {RATE_LIMIT_ABORT} consecutive UW rate limits",
                            file=sys.stderr,
                        )
                        for pending in futures:
                            pending.cancel()
                    continue
                except Exception as exc:
                    errors += 1
                    consecutive_rate_limits = 0
                    print(f"  [{idx}/{len(resolved)}] {ticker} - ERROR ({exc})", file=sys.stderr)
                    continue
                consecutive_rate_limits = 0
                if row is None:
                    no_setup += 1
                    print(f"  [{idx}/{len(resolved)}] {ticker} - NO DATA", file=sys.stderr)
                    continue
                ok += 1
                print(f"  [{idx}/{len(resolved)}] {ticker} - {row.verdict}", file=sys.stderr)
                results.append(row)
    payload = build_output(
        results, source, len(resolved), requested_tickers=resolved,
        coverage=coverage_block(
            tickers=len(resolved), ok=ok, no_setup=no_setup,
            rate_limited=rate_limited, errors=errors,
        ),
    )
    if not tickers and is_coverage_failed(payload):
        payload["scan_status"] = SCAN_STATUS_COVERAGE_FAILED
        cov = payload["coverage"]
        record_scan_degraded(
            SERVICE_NAME,
            SCAN_STATUS_COVERAGE_FAILED,
            f"UW coverage failed; completed {cov['completed']}/{cov['tickers']} ({source})",
        )
    return payload


def _read_cache_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_cache(payload: Dict[str, Any], path: Path = _CACHE_PATH) -> bool:
    payload = {key: value for key, value in payload.items() if key != "scan_status"}
    prior = _read_cache_file(path)
    if not should_persist_scan(payload, prior):
        print("Empty vol/skew MR scan — preserving last good cache", file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
    mirror_scan_snapshot(SERVICE_NAME, payload)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan for vol/skew mean-reversion tops and bottoms.")
    parser.add_argument("tickers", nargs="*", help="Tickers to scan. Overrides --preset.")
    parser.add_argument("--preset", default="ndx100", help="Preset name from data/presets (default: ndx100).")
    parser.add_argument("--limit", type=int, default=None, help="Limit tickers for smoke/validation runs.")
    parser.add_argument(
        "--workers",
        type=int,
        default=_env_int("RADON_VOL_SKEW_MR_WORKERS", DEFAULT_MAX_WORKERS),
        help=f"Concurrent workers (default {DEFAULT_MAX_WORKERS}; override RADON_VOL_SKEW_MR_WORKERS).",
    )
    parser.add_argument(
        "--retry-transient",
        action="store_true",
        help="Retry UW 429/5xx responses instead of skipping transient failures quickly.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON payload to stdout.")
    parser.add_argument("--output", default=str(_CACHE_PATH), help="Cache output path.")
    args = parser.parse_args()

    payload = scan_universe(
        args.tickers,
        preset=args.preset,
        limit=args.limit,
        max_workers=args.workers,
        retry_transient=args.retry_transient,
    )
    save_cache(payload, Path(args.output))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Vol/skew MR scan complete: "
            f"{payload['actionable_count']} actionable / {payload['candidates_found']} names"
        )


if __name__ == "__main__":
    main()
