"""Index constituent resolver — NDX / SPX / RUT via ETF holdings CSVs.

Sources (tasks/bpi-indicator-plan.md §2): QQQ (Invesco) → NDX, IVV
(iShares) → SPX, IWM (iShares) → RUT. Plain non-browser User-Agent —
never impersonate Chrome (feedback_finra_cloudflare_blocks_browser_ua).

Canonical ticker form is DASH style ("BRK-B", "MOG-A"): the same string
keys Yahoo chart fetches AND Turso ``price_history_daily`` rows, so one
form flows through the whole BPI pipeline. Dotted vendor forms ("BRK.B")
are normalized on ingest and never stored.

Fallback chain per index: fresh fetch → disk cache (any age, stale
warning to stderr) → committed seed in ``scripts/data_seeds/constituents``.
Verified 2026-07-25: the Invesco endpoint 301s to a generic ETF page and
the iShares ``.ajax`` endpoints answer plain clients with the product-page
HTML, so the live fetch currently fails closed to cache/seed; the URLs are
kept because they are the documented primary source and may serve again.
Seeds were built from api.nasdaq.com (NDX) and Vanguard's VOO/VTWO
portfolio-holding API (SPX/RUT) — see each seed's ``seed_source``.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_PROJECT_DIR = _SCRIPTS_DIR.parent

DEFAULT_CACHE_DIR = _PROJECT_DIR / "data" / "constituents"
DEFAULT_SEED_DIR = _SCRIPTS_DIR / "data_seeds" / "constituents"

USER_AGENT = "radon/2.0"  # honest non-browser UA (FINRA client precedent)
FETCH_TIMEOUT_S = 60

INDEX_SOURCES: dict[str, tuple[str, str]] = {
    "NDX": (
        "invesco",
        "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0"
        "?audienceType=Investor&action=download&ticker=QQQ",
    ),
    "SPX": (
        "ishares",
        "https://www.ishares.com/us/products/239726/ishares-core-s-p-500-etf/"
        "1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund",
    ),
    "RUT": (
        "ishares",
        "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
        "1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund",
    ),
}

# A fetch yielding fewer tickers than this is a mis-served page (HTML
# interstitial, truncated download), not a valid constituent list.
MIN_PLAUSIBLE_COUNTS = {"NDX": 80, "SPX": 400, "RUT": 1200}

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,9}$")
_INVESCO_TICKER_COLUMNS = ("holding ticker", "holdings ticker", "ticker")


class ConstituentResolutionError(RuntimeError):
    """Raised when fetch, cache, and seed all fail for an index."""


def normalize_ticker(raw: Optional[str]) -> Optional[str]:
    """Canonical dash-form ticker ("BRK.B" → "BRK-B"), or None if invalid."""
    ticker = (raw or "").strip().upper()
    ticker = re.sub(r"[./ ]", "-", ticker).strip("-")
    return ticker if _TICKER_RE.fullmatch(ticker) else None


def parse_ishares_csv(text: str) -> list[str]:
    """Equity tickers from an iShares holdings CSV (preamble + disclaimer
    lines around a ``Ticker,...,Asset Class,...`` table)."""
    rows = list(csv.reader(io.StringIO(text)))
    header_idx = next(
        (i for i, row in enumerate(rows) if row and row[0].strip() == "Ticker"), None
    )
    if header_idx is None:
        return []
    header = [cell.strip().lower() for cell in rows[header_idx]]
    if "asset class" not in header:
        return []
    asset_col = header.index("asset class")
    tickers = set()
    for row in rows[header_idx + 1:]:
        if len(row) <= asset_col or row[asset_col].strip() != "Equity":
            continue
        ticker = normalize_ticker(row[0])
        if ticker:
            tickers.add(ticker)
    return sorted(tickers)


def parse_invesco_csv(text: str) -> list[str]:
    """Tickers from an Invesco QQQ holdings CSV (all holdings are common
    equity; cash rows carry no valid ticker and normalize to None)."""
    reader = csv.DictReader(io.StringIO(text))
    fields = {name.strip().lower(): name for name in (reader.fieldnames or [])}
    column = next(
        (fields[c] for c in _INVESCO_TICKER_COLUMNS if c in fields), None
    )
    if column is None:
        return []
    tickers = {
        t for t in (normalize_ticker(row.get(column)) for row in reader) if t
    }
    return sorted(tickers)


def resolve_constituents(
    index_symbol: str,
    *,
    cache_dir: Optional[Path] = None,
    seed_dir: Optional[Path] = None,
) -> tuple[list[str], str]:
    """Member tickers for ``index_symbol`` plus the source that served them
    ("invesco" | "ishares" | "cache" | "seed")."""
    if index_symbol not in INDEX_SOURCES:
        raise ValueError(f"unknown index: {index_symbol!r}")
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    seed_dir = seed_dir or DEFAULT_SEED_DIR

    tickers, source = _try_fresh_fetch(index_symbol)
    if tickers:
        _write_cache(cache_dir, index_symbol, tickers, source)
        return tickers, source

    cached = _read_ticker_file(cache_dir / f"{index_symbol}.json")
    if cached:
        print(
            f"  constituents: serving stale cache for {index_symbol} "
            f"(fetched_at={cached.get('fetched_at')})",
            file=sys.stderr,
        )
        return cached["tickers"], "cache"

    seed = _read_ticker_file(seed_dir / f"{index_symbol}.json")
    if seed:
        print(f"  constituents: serving committed seed for {index_symbol}", file=sys.stderr)
        return seed["tickers"], "seed"

    raise ConstituentResolutionError(
        f"no constituent source available for {index_symbol} (fetch, cache, and seed all failed)"
    )


def _try_fresh_fetch(index_symbol: str) -> tuple[list[str], str]:
    source, url = INDEX_SOURCES[index_symbol]
    try:
        text = _fetch_csv(url)
    except Exception as exc:  # noqa: BLE001 — every failure falls back
        print(f"  constituents: {index_symbol} fetch failed: {exc}", file=sys.stderr)
        return [], source
    parse = parse_invesco_csv if source == "invesco" else parse_ishares_csv
    tickers = parse(text)
    minimum = MIN_PLAUSIBLE_COUNTS.get(index_symbol, 1)
    if len(tickers) < minimum:
        print(
            f"  constituents: {index_symbol} fetch implausible "
            f"({len(tickers)} tickers < {minimum}); falling back",
            file=sys.stderr,
        )
        return [], source
    return tickers, source


def _fetch_csv(url: str) -> str:
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8-sig", "replace")


def _write_cache(cache_dir: Path, index_symbol: str, tickers: list[str], source: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source,
        "tickers": tickers,
    }
    (cache_dir / f"{index_symbol}.json").write_text(json.dumps(payload, indent=1))


def _read_ticker_file(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    tickers = data.get("tickers")
    return data if isinstance(tickers, list) and tickers else None
