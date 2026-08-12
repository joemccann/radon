#!/usr/bin/env python3
"""
Index Rebalance Handler — Monitor S&P 500, NASDAQ 100, Russell 2000
for constituent changes and update presets automatically.

Runs weekly (Sunday night). Fetches fresh constituent lists, diffs
against current presets, and regenerates any that changed.

Sources:
  - S&P 500:    Wikipedia 'List of S&P 500 companies' table scrape
  - NASDAQ 100: Nasdaq official list-type JSON API
  - Russell 2000: BlackRock/iShares structured IWM holdings API

Registered as a monitor daemon handler.
"""

import json
import re
import os
import logging
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

PRESETS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "presets"
CHANGELOG_PATH = PRESETS_DIR / "changelog.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

_INDEX_VALIDATION = {
    "sp500": {"min_count": 450, "max_count": 550, "min_overlap": 0.90},
    "ndx100": {"min_count": 90, "max_count": 110, "min_overlap": 0.80},
    "r2k": {"min_count": 1500, "max_count": 2300, "min_overlap": 0.60},
}
_REQUIRED_FIELDS = {
    "sp500": ("ticker", "name", "sector", "sub_industry"),
    "ndx100": ("ticker", "name", "sector", "sub_industry"),
    "r2k": ("ticker", "name", "sector"),
}
_VALID_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_VALID_PRESET_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VALID_GROUP_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,&'()+-]{0,79}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_NAME_LENGTH = 120
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class InvalidConstituentData(ValueError):
    """Raised when an upstream constituent response is unsafe to publish."""


class _SemanticTableParser(HTMLParser):
    """Collect top-level table rows while tolerating normal HTML markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self._table_depth = 0
        self._table: Optional[List[List[str]]] = None
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._table_depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1:
            return
        if tag in {"td", "th"} and self._cell is not None:
            value = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            if self._row is not None:
                self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row and self._table is not None:
                self._table.append(self._row)
            self._row = None


def _safe_preset_key(raw_key: str) -> str:
    """Return a group key that cannot steer a preset filename out of PRESETS_DIR."""
    key = str(raw_key).strip().lower()
    if not _VALID_PRESET_KEY.fullmatch(key):
        raise InvalidConstituentData(f"unsafe preset group key: {raw_key!r}")
    return key


def _assert_within_presets_dir(path: Path) -> None:
    """Upstream-controlled text reaches these filenames, so pin containment at the write."""
    root = Path(PRESETS_DIR).resolve()
    resolved = path.resolve()
    if resolved.parent != root:
        raise InvalidConstituentData(
            f"refusing preset write outside {root}: {resolved}"
        )


def _write_json_atomic(path: Path, payload: object) -> None:
    """Replace one JSON file atomically without changing its schema."""
    _assert_within_presets_dir(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        target_mode = 0o644
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        os.fchmod(fd, target_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _is_safe_group_label(value: str) -> bool:
    """Sector / sub-industry text becomes a preset filename, so allowlist its characters."""
    return bool(_VALID_GROUP_LABEL.fullmatch(value.strip()))


def _is_safe_company_name(value: str) -> bool:
    name = value.strip()
    return bool(name) and len(name) <= _MAX_NAME_LENGTH and not _CONTROL_CHARS.search(name)


_FIELD_CHECKS = {
    "name": _is_safe_company_name,
    "sector": _is_safe_group_label,
    "sub_industry": _is_safe_group_label,
}


def validate_constituents(
    index: str, fresh_companies: List[Dict], current_tickers: List[str]
) -> None:
    """Reject truncated, misparsed, or unrelated upstream constituent data."""
    limits = _INDEX_VALIDATION[index]
    count = len(fresh_companies)
    minimum = limits["min_count"]
    maximum = limits["max_count"]
    if not minimum <= count <= maximum:
        raise InvalidConstituentData(
            f"{index}: parsed {count} constituents; expected {minimum}..{maximum}"
        )

    tickers = [company.get("ticker") for company in fresh_companies]
    invalid_tickers = [
        ticker
        for ticker in tickers
        if not isinstance(ticker, str) or not _VALID_TICKER.fullmatch(ticker)
    ]
    if invalid_tickers:
        sample = ", ".join(repr(ticker) for ticker in invalid_tickers[:5])
        raise InvalidConstituentData(f"{index}: invalid tickers in upstream data: {sample}")

    if len(set(tickers)) != len(tickers):
        raise InvalidConstituentData(f"{index}: duplicate tickers in upstream data")

    required_fields = _REQUIRED_FIELDS[index]
    incomplete = []
    for company in fresh_companies:
        if any(
            not isinstance(company.get(field), str) or not company[field].strip()
            for field in required_fields
        ):
            incomplete.append(company.get("ticker"))
    if incomplete:
        sample = ", ".join(repr(ticker) for ticker in incomplete[:5])
        raise InvalidConstituentData(
            f"{index}: missing required constituent fields for: {sample}"
        )

    unsafe = []
    for company in fresh_companies:
        for field in required_fields:
            checker = _FIELD_CHECKS.get(field)
            if checker and not checker(company[field]):
                unsafe.append((company.get("ticker"), field, company[field]))
    if unsafe:
        sample = ", ".join(
            f"{ticker} {field}={value!r}" for ticker, field, value in unsafe[:5]
        )
        raise InvalidConstituentData(f"{index}: unsafe constituent text: {sample}")

    current = {ticker for ticker in current_tickers if isinstance(ticker, str)}
    if current:
        overlap = len(current.intersection(tickers)) / len(current)
        minimum_overlap = limits["min_overlap"]
        if overlap < minimum_overlap:
            raise InvalidConstituentData(
                f"{index}: upstream overlap {overlap:.1%} is below "
                f"the {minimum_overlap:.0%} safety floor"
            )


# ─── Fetchers ────────────────────────────────────────────────

def _read_bounded(request: Request, index: str) -> str:
    """Cap third-party reads so one runaway response cannot exhaust the daemon."""
    content = urlopen(request, timeout=30).read(_MAX_RESPONSE_BYTES + 1)
    if len(content) > _MAX_RESPONSE_BYTES:
        raise InvalidConstituentData(
            f"{index}: upstream response is too large "
            f"(over {_MAX_RESPONSE_BYTES} bytes)"
        )
    return content.decode("utf-8")


def fetch_sp500() -> List[Dict]:
    """Fetch current S&P 500 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    html = _read_bounded(req, "sp500")

    parser = _SemanticTableParser()
    parser.feed(html)
    required_headers = ("symbol", "security", "gics sector", "gics sub-industry")
    rows = None
    column_indices = None
    for table in parser.tables:
        for row_index, row in enumerate(table):
            normalized = [cell.casefold().replace("‑", "-") for cell in row]
            if all(header in normalized for header in required_headers):
                column_indices = [normalized.index(header) for header in required_headers]
                rows = table[row_index + 1 :]
                break
        if rows is not None:
            break
    if rows is None or column_indices is None:
        raise InvalidConstituentData("sp500: semantic constituent table not found")

    companies = []
    max_column = max(column_indices)
    for row in rows:
        if len(row) <= max_column:
            continue
        ticker, name, sector, sub_industry = (
            row[column] for column in column_indices
        )
        if ticker:
            companies.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "sector": sector,
                    "sub_industry": sub_industry,
                }
            )

    # Dedup
    seen = set()
    unique = []
    for c in companies:
        if c["ticker"] not in seen:
            seen.add(c["ticker"])
            unique.append(c)
    return unique


def fetch_ndx100() -> List[Dict]:
    """Fetch current NASDAQ 100 constituents from Nasdaq's JSON API."""
    url = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
        },
    )
    content = _read_bounded(req, "ndx100")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InvalidConstituentData("ndx100: Nasdaq response is not valid JSON") from exc

    try:
        rows = payload["data"]["data"]["rows"]
    except (KeyError, TypeError) as exc:
        raise InvalidConstituentData("ndx100: Nasdaq constituent rows are missing") from exc
    if not isinstance(rows, list):
        raise InvalidConstituentData("ndx100: Nasdaq constituent rows are missing")

    companies = []
    for row in rows:
        if not isinstance(row, dict):
            raise InvalidConstituentData("ndx100: Nasdaq constituent row is not an object")
        ticker = str(row.get("symbol") or "").strip().upper()
        name = str(row.get("companyName") or "").strip()
        companies.append(
            {
                "ticker": ticker,
                "name": name,
                "sector": "Unknown",
                "sub_industry": "Unknown",
            }
        )

    seen = set()
    unique = []
    for c in companies:
        if c["ticker"] not in seen:
            seen.add(c["ticker"])
            unique.append(c)
    return unique


def fetch_r2k() -> List[Dict]:
    """Fetch current Russell 2000 exposure from structured IWM holdings."""
    api = (
        "https://www.blackrock.com/varnish-api/blk-one01-product-data/"
        "product-data/api/v2/get-product-data?"
    )
    params = {
        "appSubType": "ISHARES",
        "appType": "PRODUCT_PAGE",
        "component": "holdings.all",
        "locale": "en_US",
        "portfolioId": "239710",
        "targetSite": "us-ishares",
        "userType": "individual",
        "excludeContent": "true",
        "includeConfig": "true",
    }
    req = Request(
        api + urlencode(params),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    content = _read_bounded(req, "r2k")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InvalidConstituentData(
            "r2k: BlackRock response is not valid JSON"
        ) from exc

    try:
        points = payload["componentsByNameMap"]["holdings"][
            "containersByNameMap"
        ]["all"]["dataPointsByNameMap"]
        columns = {
            name: points[name]["formattedValue"]
            for name in (
                "ticker",
                "issueName",
                "sectorName",
                "assetClass",
                "holdingPercent",
            )
        }
    except (KeyError, TypeError) as exc:
        raise InvalidConstituentData(
            "r2k: BlackRock holdings columns are missing"
        ) from exc
    if any(not isinstance(values, list) for values in columns.values()):
        raise InvalidConstituentData("r2k: BlackRock holdings columns are missing")
    lengths = {len(values) for values in columns.values()}
    if len(lengths) != 1:
        raise InvalidConstituentData("r2k: BlackRock holdings column lengths differ")

    companies = []
    for ticker_value, name_value, sector_value, asset_value, weight_value in zip(
        columns["ticker"],
        columns["issueName"],
        columns["sectorName"],
        columns["assetClass"],
        columns["holdingPercent"],
    ):
        ticker = str(ticker_value or "").strip().upper()
        name = str(name_value or "").strip()
        sector = str(sector_value or "").strip()
        asset_class = str(asset_value or "").strip()
        weight_str = str(weight_value or "0").strip()

        if not ticker or ticker == "-" or asset_class != "Equity":
            continue
        excluded_name_terms = (
            "CASH COLLATERAL",
            "FUTURES",
            "ISHARES",
            "SWAP",
            "TREASURY",
        )
        if any(term in name.upper() for term in excluded_name_terms):
            continue

        try:
            weight = float(weight_str.replace(",", ""))
        except ValueError:
            weight = 0.0

        companies.append(
            {
                "ticker": ticker,
                "name": name,
                "sector": sector,
                "weight": weight,
            }
        )

    seen = set()
    unique = []
    for c in companies:
        if c["ticker"] not in seen:
            seen.add(c["ticker"])
            unique.append(c)
    return unique


# ─── Diff Logic ──────────────────────────────────────────────

def diff_tickers(
    current_preset: List[str], fresh_list: List[str]
) -> Tuple[Set[str], Set[str]]:
    """Return (added, removed) tickers."""
    current = set(current_preset)
    fresh = set(fresh_list)
    return fresh - current, current - fresh


# ─── Preset Update Logic ────────────────────────────────────

def update_sp500_presets(fresh_companies: List[Dict], added: Set[str], removed: Set[str]) -> int:
    """Update SP500 master and sub-presets. Returns number of files written."""
    master_path = PRESETS_DIR / "sp500.json"
    with open(master_path) as f:
        master = json.load(f)

    files_written = 0

    # ─── Handle removals ───
    for ticker in removed:
        # Remove from master tickers
        if ticker in master["tickers"]:
            master["tickers"].remove(ticker)

        # Remove from master pairs
        master["pairs"] = [p for p in master["pairs"] if ticker not in p]

        # Remove from groups
        for gkey, group in master["groups"].items():
            if ticker in group["tickers"]:
                group["tickers"].remove(ticker)
                group["pairs"] = [p for p in group["pairs"] if ticker not in p]

                # Re-pair orphaned partner if a pair was broken
                paired_tickers = set()
                for p in group["pairs"]:
                    paired_tickers.update(p)
                unpaired = [t for t in group["tickers"] if t not in paired_tickers]
                if len(unpaired) >= 2:
                    group["pairs"].append([unpaired[0], unpaired[1]])
                elif len(unpaired) == 1 and group["tickers"]:
                    # Pair with first ticker in group
                    group["pairs"].append([unpaired[0], group["tickers"][0]])

    # ─── Handle additions ───
    fresh_by_ticker = {c["ticker"]: c for c in fresh_companies}
    for ticker in added:
        info = fresh_by_ticker.get(ticker, {})
        sub_industry = info.get("sub_industry", "Unknown")
        sector = info.get("sector", "Unknown")

        # Add to master tickers
        if ticker not in master["tickers"]:
            master["tickers"].append(ticker)
            master["tickers"].sort()

        # Find or create the appropriate sub-industry group
        target_group = None
        for gkey, group in master["groups"].items():
            if group.get("name") == sub_industry:
                target_group = gkey
                break

        if target_group:
            group = master["groups"][target_group]
            if ticker not in group["tickers"]:
                group["tickers"].append(ticker)
                # Auto-pair with first unpaired ticker or last ticker
                paired_tickers = set()
                for p in group["pairs"]:
                    paired_tickers.update(p)
                unpaired = [t for t in group["tickers"] if t not in paired_tickers]
                if len(unpaired) >= 2:
                    new_pair = [unpaired[-2], unpaired[-1]]
                    group["pairs"].append(new_pair)
                    master["pairs"].append(new_pair)
        else:
            # New sub-industry — add to cross-industry group
            ci = master["groups"].get("cross-industry", {})
            if ci:
                if ticker not in ci.get("tickers", []):
                    ci.setdefault("tickers", []).append(ticker)

    # ─── Write master ───
    _write_json_atomic(master_path, master)
    files_written += 1

    # ─── Regenerate sub-presets ───
    for gkey, group in master["groups"].items():
        key = _safe_preset_key(gkey)
        preset = {
            "name": f"sp500-{key}",
            "description": f"S&P 500 {group['name']} ({group.get('sector', 'Multi-Sector')})",
            "tickers": group["tickers"],
            "pairs": group["pairs"],
            "sector": group.get("sector", ""),
            "sub_industry": group.get("name", ""),
            "vol_driver": group.get("vol_driver", ""),
            "source": "S&P 500 GICS classification",
        }
        sub_path = PRESETS_DIR / f"sp500-{key}.json"
        _write_json_atomic(sub_path, preset)
        files_written += 1

    # ─── Regenerate sector rollups ───
    sector_groups = {}
    for c in fresh_companies:
        s = c["sector"]
        if s not in sector_groups:
            sector_groups[s] = []
        sector_groups[s].append(c["ticker"])

    for sector_name, tickers in sector_groups.items():
        key = _safe_preset_key(sector_name.lower().replace(" ", "-"))
        sector_pairs = []
        for gkey, g in master["groups"].items():
            if g.get("sector") == sector_name:
                sector_pairs.extend(g["pairs"])

        preset = {
            "name": f"sp500-sector-{key}",
            "description": f"S&P 500 {sector_name} sector — all sub-industries",
            "tickers": sorted(tickers),
            "pairs": sector_pairs,
            "sector": sector_name,
            "source": "S&P 500 GICS classification",
        }
        _write_json_atomic(PRESETS_DIR / f"sp500-sector-{key}.json", preset)
        files_written += 1

    return files_written


def update_ndx100_presets(fresh_companies: List[Dict], added: Set[str], removed: Set[str]) -> int:
    """Update NDX100 master and sub-presets."""
    master_path = PRESETS_DIR / "ndx100.json"
    with open(master_path) as f:
        master = json.load(f)

    # Update master tickers
    fresh_tickers = sorted(set(c["ticker"] for c in fresh_companies))
    master["tickers"] = fresh_tickers

    # Handle removals from groups
    for ticker in removed:
        master["pairs"] = [p for p in master["pairs"] if ticker not in p]
        for gkey, group in master["groups"].items():
            if ticker in group["tickers"]:
                group["tickers"].remove(ticker)
                group["pairs"] = [p for p in group["pairs"] if ticker not in p]

    # Handle additions — add to the closest matching group
    for ticker in added:
        # Default: add to misc-singles group
        misc = master["groups"].get("misc-singles")
        if misc and ticker not in misc["tickers"]:
            misc["tickers"].append(ticker)

    # Rebuild master pairs from groups
    master["pairs"] = []
    for g in master["groups"].values():
        master["pairs"].extend(g["pairs"])

    master["description"] = (
        f"NASDAQ 100 — {len(fresh_tickers)} companies, "
        f"{len(master['groups'])} groups, {len(master['pairs'])} curated pairs"
    )
    master["source"] = (
        "Nasdaq official Nasdaq-100 API, " + datetime.now().strftime("%B %Y")
    )

    files_written = 0
    _write_json_atomic(master_path, master)
    files_written += 1

    # Regenerate sub-presets
    for gkey, group in master["groups"].items():
        key = _safe_preset_key(gkey)
        preset = {
            "name": f"ndx100-{key}",
            "description": f"NASDAQ 100 {group['name']}",
            "tickers": group["tickers"],
            "pairs": group["pairs"],
            "sector": group.get("sector", ""),
            "vol_driver": group.get("vol_driver", ""),
            "source": "Nasdaq official Nasdaq-100 API",
        }
        _write_json_atomic(PRESETS_DIR / f"ndx100-{key}.json", preset)
        files_written += 1

    return files_written


def update_r2k_presets(fresh_companies: List[Dict], added: Set[str], removed: Set[str]) -> int:
    """Update R2K master and sub-presets."""
    master_path = PRESETS_DIR / "r2k.json"

    # Rebuild from scratch (R2K changes too many tickers to diff incrementally)
    sectors = {}
    for c in fresh_companies:
        s = c["sector"]
        if s not in sectors:
            sectors[s] = []
        sectors[s].append(c)

    # Sort by weight within each sector
    for s in sectors:
        sectors[s].sort(key=lambda x: -x.get("weight", 0))

    r2k_groups = {}
    all_pairs = []

    for sector_name, members in sorted(sectors.items(), key=lambda x: -len(x[1])):
        tickers = [m["ticker"] for m in members]
        pairs = []
        for i in range(0, len(tickers) - 1, 2):
            pairs.append([tickers[i], tickers[i + 1]])
        if len(tickers) % 2 == 1:
            pairs.append([tickers[-1], tickers[0]])

        key = sector_name.lower().replace(" ", "-").replace("&", "and")
        if not key:
            key = "other"
        key = _safe_preset_key(key)

        r2k_groups[key] = {
            "name": sector_name,
            "sector": sector_name,
            "tickers": tickers,
            "pairs": pairs,
            "vol_driver": f"Russell 2000 {sector_name} — small-cap sector fundamentals",
        }
        all_pairs.extend(pairs)

    # Tier groups
    sorted_all = sorted(fresh_companies, key=lambda x: -x.get("weight", 0))
    tiers = {
        "top-100": (0, 100),
        "top-200": (0, 200),
        "top-500": (0, 500),
        "mid-500": (100, 600),
        "tail-500": (max(0, len(sorted_all) - 500), len(sorted_all)),
    }

    for tier_key, (start, end) in tiers.items():
        tier_companies = sorted_all[start:min(end, len(sorted_all))]
        tier_tickers = [c["ticker"] for c in tier_companies]
        tier_pairs = []
        for i in range(0, len(tier_tickers) - 1, 2):
            tier_pairs.append([tier_tickers[i], tier_tickers[i + 1]])

        r2k_groups[f"tier-{tier_key}"] = {
            "name": f"R2K {tier_key.replace('-', ' ').title()}",
            "sector": "Multi-Sector",
            "tickers": tier_tickers,
            "pairs": tier_pairs,
            "vol_driver": f"Small-cap factor exposure",
        }

    all_tickers = sorted(set(c["ticker"] for c in fresh_companies))

    master = {
        "name": "r2k",
        "description": f"Russell 2000 (IWM) — {len(all_tickers)} companies, {len(sectors)} sectors, {len(all_pairs)} pairs",
        "tickers": all_tickers,
        "pairs": all_pairs,
        "source": f"iShares Russell 2000 ETF (IWM) holdings, {datetime.now().strftime('%B %Y')}",
        "groups": r2k_groups,
    }

    files_written = 0
    _write_json_atomic(master_path, master)
    files_written += 1

    for raw_key, g in sorted(r2k_groups.items()):
        key = _safe_preset_key(raw_key)
        preset = {
            "name": f"r2k-{key}",
            "description": f"Russell 2000 {g['name']} ({len(g['tickers'])} companies)",
            "tickers": g["tickers"],
            "pairs": g["pairs"],
            "sector": g["sector"],
            "vol_driver": g["vol_driver"],
            "source": "iShares Russell 2000 ETF (IWM) holdings",
        }
        _write_json_atomic(PRESETS_DIR / f"r2k-{key}.json", preset)
        files_written += 1

    return files_written


# ─── Changelog ───────────────────────────────────────────────

def log_changes(index: str, added: Set[str], removed: Set[str]):
    """Append to changelog.json."""
    changelog = []
    if CHANGELOG_PATH.exists():
        try:
            with open(CHANGELOG_PATH) as f:
                changelog = json.load(f)
        except (json.JSONDecodeError, KeyError):
            changelog = []

    entry = {
        "timestamp": datetime.now().isoformat(),
        "index": index,
        "added": sorted(added),
        "removed": sorted(removed),
        "added_count": len(added),
        "removed_count": len(removed),
    }
    changelog.append(entry)

    # Keep last 100 entries
    changelog = changelog[-100:]

    _write_json_atomic(CHANGELOG_PATH, changelog)


# ─── Main Handler ────────────────────────────────────────────

def execute() -> dict:
    """
    Check all three indices for constituent changes.
    Called by the monitor daemon weekly.

    Returns:
        dict with status, changes detected, files written.
    """
    results = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "indices": {},
        "total_changes": 0,
        "total_files_written": 0,
    }

    sources = (
        ("sp500", "S&P 500", "SP500", fetch_sp500, update_sp500_presets),
        ("ndx100", "NASDAQ 100", "NDX100", fetch_ndx100, update_ndx100_presets),
        ("r2k", "Russell 2000", "R2K", fetch_r2k, update_r2k_presets),
    )
    prepared = {}
    validation_errors = []

    # Fetch and validate every source before allowing any canonical write. A
    # single malformed provider response aborts the whole publish phase.
    for index, display_name, log_name, fetcher, updater in sources:
        try:
            logger.info("Fetching %s constituents...", display_name)
            fresh_companies = fetcher()
            with open(PRESETS_DIR / f"{index}.json", encoding="utf-8") as handle:
                current = json.load(handle)
            current_tickers = current.get("tickers")
            if not isinstance(current_tickers, list):
                raise InvalidConstituentData(
                    f"{index}: canonical preset has no ticker list"
                )
            validate_constituents(index, fresh_companies, current_tickers)
            fresh_tickers = [company["ticker"] for company in fresh_companies]
            added, removed = diff_tickers(current_tickers, fresh_tickers)
            prepared[index] = (fresh_companies, added, removed, updater, log_name)
            results["indices"][index] = {
                "added": sorted(added),
                "removed": sorted(removed),
                "files_written": 0,
                "validated": True,
            }
        except Exception as exc:
            message = str(exc)
            logger.error("%s fetch/validation failed: %s", log_name, message)
            results["indices"][index] = {"error": message}
            validation_errors.append(f"{index}: {message}")

    if validation_errors:
        results["status"] = "error"
        results["error"] = "Unsafe constituent data; no presets written: " + "; ".join(
            validation_errors
        )
        logger.error("Preset rebalance aborted before writes: %s", results["error"])
        return results

    for index, _, _, _, _ in sources:
        fresh_companies, added, removed, updater, log_name = prepared[index]
        if not added and not removed:
            logger.info("%s: No changes", log_name)
            continue

        try:
            logger.info(
                "%s: +%s -%s changes detected", log_name, len(added), len(removed)
            )
            files = updater(fresh_companies, added, removed)
            log_changes(index, added, removed)
            results["indices"][index]["files_written"] = files
            results["total_changes"] += len(added) + len(removed)
            results["total_files_written"] += files
        except Exception as exc:
            message = f"{index} update failed: {exc}"
            logger.error(message)
            results["indices"][index] = {"error": str(exc)}
            results["status"] = "error"
            results["error"] = message
            break

    return results


# ─── CLI ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Index Rebalance Checker")
    parser.add_argument("--dry-run", action="store_true", help="Check only, don't update")
    parser.add_argument("--index", choices=["sp500", "ndx100", "r2k"], help="Check single index")
    parser.add_argument("--changelog", action="store_true", help="Show recent changelog")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.changelog:
        if CHANGELOG_PATH.exists():
            with open(CHANGELOG_PATH) as f:
                entries = json.load(f)
            print(f"\n=== PRESET CHANGELOG ({len(entries)} entries) ===\n")
            for e in entries[-10:]:
                print(f"  {e['timestamp'][:19]}  {e['index']:8s}  +{e['added_count']} -{e['removed_count']}")
                if e["added"]:
                    print(f"    Added: {', '.join(e['added'][:10])}")
                if e["removed"]:
                    print(f"    Removed: {', '.join(e['removed'][:10])}")
        else:
            print("No changelog yet.")
        exit(0)

    if args.dry_run:
        print("\n=== DRY RUN — checking for changes ===\n")

        checks = {
            "sp500": (fetch_sp500, "sp500.json"),
            "ndx100": (fetch_ndx100, "ndx100.json"),
            "r2k": (fetch_r2k, "r2k.json"),
        }

        indices = [args.index] if args.index else ["sp500", "ndx100", "r2k"]

        for idx in indices:
            fetcher, master_file = checks[idx]
            try:
                fresh = fetcher()
                fresh_tickers = [c["ticker"] for c in fresh]
                with open(PRESETS_DIR / master_file) as f:
                    current = json.load(f)
                validate_constituents(idx, fresh, current["tickers"])
                added, removed = diff_tickers(current["tickers"], fresh_tickers)

                if added or removed:
                    print(f"  {idx.upper():8s}: ⚠️  CHANGES DETECTED")
                    if added:
                        print(f"    + Added ({len(added)}): {', '.join(sorted(added)[:20])}")
                    if removed:
                        print(f"    - Removed ({len(removed)}): {', '.join(sorted(removed)[:20])}")
                else:
                    print(f"  {idx.upper():8s}: ✅ No changes ({len(fresh_tickers)} tickers)")
            except Exception as e:
                print(f"  {idx.upper():8s}: ❌ Error — {e}")
    else:
        result = execute()
        print(f"\n=== INDEX REBALANCE CHECK ===\n")
        if result.get("status") == "error":
            print(f"  ERROR: {result.get('error', 'rebalance failed')}")
        print(f"  Total changes: {result['total_changes']}")
        print(f"  Files written: {result['total_files_written']}")
        for idx, info in result["indices"].items():
            if "error" in info:
                print(f"  {idx}: ❌ {info['error']}")
            else:
                added = len(info.get("added", []))
                removed = len(info.get("removed", []))
                if added or removed:
                    print(f"  {idx}: +{added} -{removed}")
                else:
                    print(f"  {idx}: ✅ No changes")
        if result.get("status") == "error":
            raise SystemExit(1)
