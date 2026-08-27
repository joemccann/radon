"""CTA percentile scale reconciliation.

MenthorQ renders the percentile columns as 0-1 fractions on some cards and as
0-100 integers on others, and the vision extractor is told to report integers.
When it obeys on a fractional card it rounds 0.43 to 0 and 0.98 to 1, so a
max-LONG row lands in the payload as "0th percentile" and every narrative built
on it inverts — that is the 2026-08-25 CTA page reporting SPX at the 0th
percentile off a +3.66 long carrying a +1.48 z-score.

The z-score in the same row is extracted independently and is never rounded,
which makes it the check: percentile_3m and z_score_3m measure the same
position against the same 3M window, so they cannot disagree.

Mirrors `web/lib/ctaPercentiles.ts` — keep the two in step.
"""

from __future__ import annotations

import math
from typing import Any, Optional

PERCENTILE_FIELDS = ("percentile_1m", "percentile_3m", "percentile_1y")

# Widest gap seen between a sound percentile and the one its z-score implies,
# across every menthorq_cta payload on record, is ~24 points (thin-tailed
# series like Natural Gas). 35 sits clear of that and still catches every
# rounded row that inverts a narrative.
Z_DISAGREEMENT_LIMIT = 35


def normalize_pctile(p: Any) -> int:
    """Percentile as 0-100. The main table ships 0-100 ints; the index and
    commodity tables ship 0-1 fractions. Both reach this module.

    Disambiguate by TYPE, not by range: an int 1 is the 1st percentile (max
    short) while a float 1.0 is the 100th (max long). A range test alone reads
    them identically and silently inverts the entire narrative.
    """
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return 50
    if isinstance(p, int):
        return max(0, min(100, p))
    return int(round(p * 100)) if 0.0 <= p <= 1.0 else int(round(p))


def percentile_from_z(z: Any) -> Optional[float]:
    """The percentile a 3M z-score implies, 0-100."""
    if isinstance(z, bool) or not isinstance(z, (int, float)) or not math.isfinite(z):
        return None
    return 50.0 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _row_key(row: dict) -> tuple:
    """Same contract in two tables is the same row: name, position and z all match."""
    name = str(row.get("underlying") or "").strip().lower()
    return (name, _num(row.get("position_today")), _num(row.get("z_score_3m")))


def _normalized_trio(row: dict) -> list[Optional[int]]:
    """Scale is a property of the ROW, not of a single cell. A card that renders
    fractions renders them in every column, so a 1.0 sitting beside a 0.98 is
    the 100th percentile — reading that cell on its own calls it the 1st and
    inverts the row.
    """
    raw = [_num(row.get(field)) for field in PERCENTILE_FIELDS]
    present = [v for v in raw if v is not None]
    fractional = bool(present) and all(0.0 <= v <= 1.0 for v in present) and any(
        not float(v).is_integer() for v in present
    )
    return [
        None if v is None else max(0, min(100, int(round(v * 100 if fractional else v))))
        for v in raw
    ]


def _rounded_rank(trio: list[Optional[int]]) -> int:
    """How much a trio looks like a fractional card read as integers: rounding
    collapses every column onto 0 or 1. Lower is more informative.
    """
    present = [v for v in trio if v is not None]
    if not present:
        return 2
    return 1 if all(v in (0, 1) for v in present) else 0


def _candidate_rank(trio: list[Optional[int]], gap: float) -> tuple:
    """Lower wins. A z-score that can arbitrate always beats one that cannot;
    among rows no z-score can arbitrate, the trio that survived rounding wins.
    Without this, `inf < inf` is False and whichever table came first wins —
    which republishes the rounded row over the good one.
    """
    finite = math.isfinite(gap)
    return (0 if finite else 1, gap if finite else 0.0, _rounded_rank(trio))


def reconcile_tables(tables: Optional[dict]) -> Optional[dict]:
    """Normalize every percentile to 0-100, repair a row whose percentiles were
    rounded away by copying the same row from another table, and null out what
    neither survives: a percentile its own z-score flatly contradicts is not a
    number to publish.
    """
    if not tables:
        return tables if tables is None else {}

    best: dict[tuple, tuple[list[Optional[int]], tuple]] = {}
    for rows in tables.values():
        for row in rows or []:
            trio = _normalized_trio(row)
            implied = percentile_from_z(row.get("z_score_3m"))
            gap = (
                math.inf
                if implied is None or trio[1] is None
                else abs(trio[1] - implied)
            )
            key = _row_key(row)
            rank = _candidate_rank(trio, gap)
            if key not in best or rank < best[key][1]:
                best[key] = (trio, rank)

    out: dict[str, list[dict]] = {}
    for table, rows in tables.items():
        repaired = []
        for row in rows or []:
            chosen = best.get(_row_key(row))
            trio = chosen[0] if chosen else _normalized_trio(row)
            implied = percentile_from_z(row.get("z_score_3m"))
            contradicted = (
                implied is not None
                and trio[1] is not None
                and abs(trio[1] - implied) > Z_DISAGREEMENT_LIMIT
            )
            new_row = dict(row)
            for field, value in zip(PERCENTILE_FIELDS, trio):
                new_row[field] = None if contradicted else value
            repaired.append(new_row)
        out[table] = repaired
    return out
