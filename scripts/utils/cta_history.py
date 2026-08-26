"""Prior-session CTA payloads: the only admissible source of "what changed".

The share copy is data-driven but not, on its own, distinct: two sessions in
the same regime hit the same branch and read the same. What separates them is
history — which instruments crossed an extreme, how far SPX moved, how long
the regime has run — and every one of those facts requires a payload from an
earlier session to compare against.

Dated payloads already exist in two places, so nothing new has to be
persisted: the Turso `menthorq_cta` rows and the `data/menthorq_cache/
cta_YYYY-MM-DD.json` files written by the sync. When neither yields a prior
session the derivations return None / empty and the caller drops the sentence.
A delta is never estimated, and never carried over from a different date.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional

from utils.cta_percentiles import reconcile_tables

PROJECT_DIR = Path(__file__).resolve().parents[2]
CTA_CACHE_DIR = PROJECT_DIR / "data" / "menthorq_cache"

# Bounded on purpose: the reads go over the direct-to-cloud HTTP pipeline, and
# a regime run older than this is reported as a floor rather than a count.
HISTORY_LOOKBACK = 12

TRACKED_TABLES = ("main", "index", "commodity", "currency")

_NAME_NOISE = ("E-Mini ", "CME ", "ICE MSCI ", "Micro ", " Index", " Futures")


def reconciled_payload(data: dict) -> dict:
    """A prior session is only comparable to today once its percentiles sit on
    the same repaired scale — otherwise a rounded-away 0 in the archive reads as
    a regime change that never happened."""
    if data and data.get("tables"):
        return {**data, "tables": reconcile_tables(data["tables"])}
    return data


def payload_is_valid(data: object) -> bool:
    """A payload is usable only when its main table actually carries rows."""
    if not isinstance(data, dict):
        return False
    tables = data.get("tables")
    if not isinstance(tables, dict):
        return False
    main = tables.get("main")
    return isinstance(main, list) and len(main) > 0


def clean_underlying_name(underlying: str) -> str:
    """Ticker-desk shorthand for a MenthorQ underlying label."""
    name = underlying or ""
    for noise in _NAME_NOISE:
        name = name.replace(noise, "")
    return name.strip()


# ── Loading ───────────────────────────────────────────────────────────────────

def _load_history_from_db(current_date: str, limit: int) -> list[dict]:
    try:
        from db.client import get_db  # lazy: disk fallback must work without libsql

        rows = get_db().execute(
            "SELECT date, payload FROM menthorq_cta WHERE date < ? ORDER BY date DESC LIMIT ?",
            (current_date, limit),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — any DB failure falls back to disk
        print(f"[cta-share] history db read unavailable: {exc}", file=sys.stderr)
        return []

    payloads = []
    for row in rows or []:
        try:
            data = json.loads(row[1])
        except Exception:  # noqa: BLE001 — a corrupt row must not sink the post
            continue
        if payload_is_valid(data):
            data.setdefault("date", row[0])
            payloads.append(reconciled_payload(data))
    return payloads


def _load_history_from_disk(current_date: str, cache_dir: Path, limit: int) -> list[dict]:
    try:
        files = sorted(Path(cache_dir).glob("cta_????-??-??.json"))
    except OSError:
        return []

    payloads = []
    for path in reversed(files):
        data_date = path.stem[len("cta_"):]
        if data_date >= current_date:
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            continue
        if not payload_is_valid(data):
            continue
        data.setdefault("date", data_date)
        payloads.append(reconciled_payload(data))
        if len(payloads) >= limit:
            break
    return payloads


def load_prior_payloads(
    current_date: str,
    *,
    cache_dir: Path = CTA_CACHE_DIR,
    limit: int = HISTORY_LOOKBACK,
) -> list[dict]:
    """Sessions strictly older than `current_date`, newest first.

    Same source precedence as the page itself: Turso first, disk cache
    underneath, deduplicated by date with the DB row winning.
    """
    if not current_date:
        return []

    by_date: dict[str, dict] = {}
    for data in (
        *_load_history_from_disk(current_date, cache_dir, limit),
        *_load_history_from_db(current_date, limit),
    ):
        data_date = data.get("date")
        if data_date and data_date < current_date:
            by_date[data_date] = data

    return [by_date[d] for d in sorted(by_date, reverse=True)][:limit]


# ── Derivation ────────────────────────────────────────────────────────────────

def _rows(payload: dict) -> Iterable[dict]:
    tables = (payload or {}).get("tables") or {}
    for name in TRACKED_TABLES:
        for row in tables.get(name) or []:
            if isinstance(row, dict) and row.get("underlying"):
                yield row


def extreme_names(payload: dict, *, assess: Callable[[dict], dict]) -> set[str]:
    """Every instrument the shared assessment calls extreme in this payload."""
    return {
        clean_underlying_name(row["underlying"])
        for row in _rows(payload)
        if assess(row)["is_extreme"]
    }


def regime_label(
    payload: dict,
    *,
    assess: Callable[[dict], dict],
    find_spx: Callable[[dict], Optional[dict]],
) -> str:
    spx = find_spx(payload)
    if not spx:
        return "unknown"
    a = assess(spx)
    if a["is_extreme"] and a["side"] == "long":
        return "max long"
    if a["is_extreme"] and a["side"] == "short":
        return "max short"
    return "in-range"


def _spx_delta(
    current: dict,
    prior: dict,
    *,
    assess: Callable[[dict], dict],
    find_spx: Callable[[dict], Optional[dict]],
) -> Optional[dict]:
    now_row, prior_row = find_spx(current), find_spx(prior)
    if not now_row or not prior_row:
        return None
    now, was = assess(now_row), assess(prior_row)
    return {
        "position": now["today"],
        "prior_position": was["today"],
        "z": now["z"],
        "prior_z": was["z"],
        "pctile": now["pctile"],
        "prior_pctile": was["pctile"],
    }


def _regime_run(
    current: dict,
    priors: list,
    *,
    assess: Callable[[dict], dict],
    find_spx: Callable[[dict], Optional[dict]],
) -> tuple[int, bool]:
    """(sessions in the current run, whether the run's start was observed)."""
    label = regime_label(current, assess=assess, find_spx=find_spx)
    sessions = 1
    for prior in priors:
        if regime_label(prior, assess=assess, find_spx=find_spx) != label:
            return sessions, True
        sessions += 1
    return sessions, False


def derive_change_context(
    current: dict,
    priors: list,
    *,
    assess: Callable[[dict], dict],
    find_spx: Callable[[dict], Optional[dict]],
) -> dict:
    """What is true of THIS session that was not true of the last one."""
    label = regime_label(current, assess=assess, find_spx=find_spx)
    context = {
        "prior_date": None,
        "entered_extreme": [],
        "exited_extreme": [],
        "spx_delta": None,
        "regime_label": label,
        "regime_sessions": None,
        "regime_bounded": False,
    }
    if not priors:
        return context

    prior = priors[0]
    now_extremes = extreme_names(current, assess=assess)
    prior_extremes = extreme_names(prior, assess=assess)
    sessions, bounded = _regime_run(current, priors, assess=assess, find_spx=find_spx)

    context.update(
        prior_date=prior.get("date"),
        entered_extreme=sorted(now_extremes - prior_extremes),
        exited_extreme=sorted(prior_extremes - now_extremes),
        spx_delta=_spx_delta(current, prior, assess=assess, find_spx=find_spx),
        regime_sessions=sessions,
        regime_bounded=bounded,
    )
    return context
