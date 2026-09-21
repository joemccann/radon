#!/usr/bin/env python3
"""Retro-cleanup for legacy Flex AGGREGATE journal rows (PR #569 follow-up).

Two legacy classes exist in the Turso ``journal`` table, both written before
``journal_rehydrate`` started stamping ``fill_breakdown`` on the rows it
collapses:

  A. **Multi-day aggregate without ``fill_breakdown``** — a ``+``-joined /
     ``CLOSED`` row whose constituent executions happened on more than one ET
     session. ``journal_basis.flex_aggregate_budget`` falls back to the row's
     own date for such a row, so the aggregate only budgets its FIRST date and
     the fills on every later date are journaled beside it and double-counted.
     Fix: reconstruct ``fill_breakdown`` from the individual journal fills the
     aggregate covers, so the budget lands on the right days.

  B. **Aggregate sitting beside individual fills** — every date/sign the
     aggregate accounts for is already covered, in full, by individual fill
     rows. The aggregate is pure duplication. Fix: delete it.

Both are derived from the journal itself; nothing is guessed. An aggregate
whose covering fills cannot be reconstructed exactly is reported and left
alone.

DRY RUN BY DEFAULT. ``--apply`` is destructive and is the operator's step.

    python -m scripts.cleanup_legacy_flex_aggregates            # dry run
    python -m scripts.cleanup_legacy_flex_aggregates --apply    # operator only
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable, Optional

try:  # package import (python -m scripts.…)
    from scripts.clients.journal_basis import (
        contract_fill_fingerprint,
        flex_aggregate_budget,
    )
except ImportError:  # direct script execution
    from clients.journal_basis import (  # type: ignore[no-redef]
        contract_fill_fingerprint,
        flex_aggregate_budget,
    )


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def is_aggregate(payload: dict[str, Any]) -> bool:
    """True for a row ``flex_aggregate_budget`` treats as an aggregate."""
    exec_id = str(payload.get("ib_exec_id") or "")
    action = str(payload.get("action") or "").strip().upper()
    return "+" in exec_id or action == "CLOSED"


def _individual_coverage(rows: Iterable[tuple[str, dict[str, Any]]]) -> dict:
    """``{contract: {sign: {date: qty}}}`` from individual (non-aggregate) fills."""
    cover: dict[str, dict[int, dict[str, float]]] = {}
    for _trade_id, payload in rows:
        fingerprint = contract_fill_fingerprint(payload)
        if not fingerprint:
            continue
        contract, date, signed = fingerprint
        day = str(date)[:10]
        if not day:
            continue
        sign = 1 if signed > 0 else -1
        by_sign = cover.setdefault(contract, {}).setdefault(sign, {})
        by_sign[day] = by_sign.get(day, 0.0) + abs(signed)
    return cover


def _reconstruct_side(
    coverage: dict[str, float], row_day: str, needed: float
) -> Optional[dict[str, float]]:
    """Earliest dates from ``row_day`` on whose totals sum EXACTLY to ``needed``.

    Returns None when no such prefix exists — the aggregate then stays as it is
    rather than being re-dated on a guess.
    """
    if needed <= 0:
        return None
    running = 0.0
    taken: dict[str, float] = {}
    for day in sorted(d for d in coverage if d >= row_day):
        taken[day] = coverage[day]
        running += coverage[day]
        if abs(running - needed) < 1e-6:
            return taken
        if running > needed:
            return None
    return None


def plan_cleanup(rows: Iterable[tuple[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Classify every legacy Flex aggregate row.

    ``rows`` are ``(trade_id, payload)`` pairs straight off ``journal``.
    Returns ``{"delete": [...], "redate": [...], "unreconstructable": [...]}``.
    Rows outside the two classes never appear in any bucket.
    """
    parsed = [(trade_id, _payload(raw)) for trade_id, raw in rows]
    aggregates = [item for item in parsed if is_aggregate(item[1])]
    individuals = [item for item in parsed if not is_aggregate(item[1])]
    coverage = _individual_coverage(individuals)

    plan: dict[str, list[dict[str, Any]]] = {
        "delete": [],
        "redate": [],
        "unreconstructable": [],
    }

    for trade_id, payload in aggregates:
        if payload.get("fill_breakdown"):
            continue  # already stamped by rehydrate — idempotent no-op
        budget = flex_aggregate_budget(payload) or {}
        if not budget:
            continue
        row_day = str(payload.get("date") or "")[:10]
        if not row_day:
            continue

        breakdown: list[dict[str, Any]] = []
        fully_covered = True
        ambiguous = False
        for (contract, _day, sign), qty in sorted(budget.items(), key=lambda kv: str(kv[0])):
            side = {
                day: day_qty
                for day, day_qty in coverage.get(contract, {}).get(sign, {}).items()
                if day >= row_day
            }
            if not side:
                # This leg is not journaled individually at all: keep the
                # legacy single-date fallback for it and change nothing.
                fully_covered = False
                breakdown.append({"date": row_day, "qty": qty * sign})
                continue
            taken = _reconstruct_side(side, row_day, qty)
            if taken is None:
                # Individual fills exist but do not add up to the aggregate.
                # Re-dating would be a guess, so the row is reported instead.
                ambiguous = True
                break
            for day, day_qty in sorted(taken.items()):
                breakdown.append({"date": day, "qty": day_qty * sign})
        breakdown.sort(key=lambda item: (item["date"], item["qty"]))

        if ambiguous:
            plan["unreconstructable"].append(
                {"trade_id": trade_id, "date": row_day, "budget": _budget_repr(budget)}
            )
            continue

        days = {item["date"] for item in breakdown}
        if fully_covered:
            plan["delete"].append(
                {
                    "trade_id": trade_id,
                    "date": row_day,
                    "reason": "every date/sign already covered by individual fills",
                    "breakdown": breakdown,
                }
            )
        elif days - {row_day}:
            plan["redate"].append(
                {
                    "trade_id": trade_id,
                    "date": row_day,
                    "reason": "multi-day aggregate missing fill_breakdown",
                    "breakdown": breakdown,
                }
            )
    return plan


def _budget_repr(budget: dict) -> list[list[Any]]:
    return [[contract, day, sign, qty] for (contract, day, sign), qty in sorted(budget.items(), key=str)]


def render_plan(plan: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = []
    lines.append(f"delete (class B, aggregate beside individual fills): {len(plan['delete'])}")
    for item in plan["delete"]:
        lines.append(f"  DELETE {item['trade_id']}  date={item['date']}  {item['reason']}")
    lines.append(f"redate (class A, multi-day aggregate w/o fill_breakdown): {len(plan['redate'])}")
    for item in plan["redate"]:
        lines.append(
            f"  SET fill_breakdown {item['trade_id']}  date={item['date']}  "
            f"{json.dumps(item['breakdown'])}"
        )
    lines.append(f"left alone (cannot reconstruct from journal): {len(plan['unreconstructable'])}")
    for item in plan["unreconstructable"]:
        lines.append(f"  SKIP {item['trade_id']}  date={item['date']}")
    return "\n".join(lines)


def _connect():
    from dotenv import dotenv_values
    import os
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    env: dict[str, Any] = {}
    for candidate in (root / ".env", root / "web" / ".env"):
        if candidate.exists():
            env.update({k: v for k, v in dotenv_values(candidate).items() if v})
    url = os.environ.get("TURSO_DB_URL") or env.get("TURSO_DB_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN") or env.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise SystemExit("TURSO_DB_URL and TURSO_AUTH_TOKEN must be set")
    import libsql_experimental as libsql

    return libsql.connect(database=url, auth_token=token)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="DESTRUCTIVE: perform the deletes / fill_breakdown writes",
    )
    args = parser.parse_args(argv)

    db = _connect()
    rows = db.execute("SELECT trade_id, payload FROM journal").fetchall()
    plan = plan_cleanup([(row[0], row[1]) for row in rows])

    print(f"journal rows scanned: {len(rows)}")
    print(render_plan(plan))

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to perform it.")
        return 0

    for item in plan["delete"]:
        db.execute("DELETE FROM journal WHERE trade_id = ?", (item["trade_id"],))
    for item in plan["redate"]:
        current = db.execute(
            "SELECT payload FROM journal WHERE trade_id = ?", (item["trade_id"],)
        ).fetchall()
        if not current:
            continue
        payload = _payload(current[0][0])
        if payload.get("fill_breakdown"):
            continue
        payload["fill_breakdown"] = item["breakdown"]
        db.execute(
            "UPDATE journal SET payload = ? WHERE trade_id = ?",
            (json.dumps(payload), item["trade_id"]),
        )
    db.commit()
    print(
        f"\nAPPLIED: deleted {len(plan['delete'])}, "
        f"stamped fill_breakdown on {len(plan['redate'])}."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
