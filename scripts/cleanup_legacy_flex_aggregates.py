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
     Repair requires authoritative constituent executions; partial journal
     coverage is reported for operator reconciliation without guessing dates.

  B. **Aggregate sitting beside individual fills** — exact, exclusive execution
     identities, contract, account, date and quantities prove full coverage.
     Only this proven duplicate can be deleted.

Execution IDs from different writer namespaces, corrections, overlapping
claims and incomplete histories cannot prove equivalence. They are reported
and left alone, even when contract and quantity happen to match.

DRY RUN BY DEFAULT. ``--apply`` is destructive and is the operator's step.

    python -m scripts.cleanup_legacy_flex_aggregates            # dry run
    python -m scripts.cleanup_legacy_flex_aggregates --apply    # operator only
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
import math
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


def _parts(payload: dict[str, Any]) -> list[str]:
    return [part.strip() for part in str(payload.get("ib_exec_id") or "").split("+")]


def _account(payload: dict[str, Any]) -> tuple:
    return tuple(payload.get(key) for key in ("account", "account_id", "ib_account"))


def plan_cleanup(rows: Iterable[tuple[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Delete only aggregates whose exact constituents are exclusively proven.

    Flex tradeIDs and API execIds are different namespaces. Quantity/date
    similarity is insufficient evidence for a destructive repair. Missing,
    corrected, duplicated or shared identities require authoritative operator
    reconciliation; partial coverage never authorizes a metadata rewrite.
    """
    parsed = [(trade_id, _payload(raw)) for trade_id, raw in rows]
    aggregates = [item for item in parsed if is_aggregate(item[1])]
    individuals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    claims: Counter = Counter()
    for _, payload in aggregates:
        claims.update(set(_parts(payload)))
    for _, payload in parsed:
        if not is_aggregate(payload):
            individuals[str(payload.get("ib_exec_id") or "").strip()].append(payload)

    plan: dict[str, list[dict[str, Any]]] = {
        "delete": [], "redate": [], "unreconstructable": [],
    }
    for trade_id, payload in aggregates:
        if payload.get("fill_breakdown"):
            continue
        row_day = str(payload.get("date") or "")[:10]
        budget = {}
        parts = _parts(payload)
        try:
            date.fromisoformat(row_day)
            budget = flex_aggregate_budget(payload) or {}
            if (not budget or not all(parts) or len(parts) != len(set(parts))
                    or any(claims[part] != 1 or len(individuals[part]) != 1 for part in parts)):
                raise ValueError("constituent identity is missing, duplicated or shared")
            expected: Counter = Counter()
            for (contract, _, sign), qty in budget.items():
                if not math.isfinite(qty) or qty <= 0:
                    raise ValueError("invalid aggregate quantity")
                expected[contract, sign] += qty
            actual: Counter = Counter()
            dated: Counter = Counter()
            for part in parts:
                individual = individuals[part][0]
                fingerprint = contract_fill_fingerprint(individual)
                if not fingerprint or _account(individual) != _account(payload):
                    raise ValueError("unproven contract or account")
                contract, raw_day, signed = fingerprint
                day = date.fromisoformat(str(raw_day)[:10]).isoformat()
                if not math.isfinite(signed) or signed == 0:
                    raise ValueError("invalid constituent quantity")
                sign = 1 if signed > 0 else -1
                actual[contract, sign] += abs(signed)
                dated[contract, day, sign] += abs(signed)
            if (set(actual) != set(expected)
                    or any(not math.isclose(actual[key], qty, rel_tol=0, abs_tol=1e-6)
                           for key, qty in expected.items())
                    or min(day for _, day, _ in dated) != row_day):
                raise ValueError("constituents disagree with aggregate coverage")
            if "gross_fill_breakdown" in payload and dated != budget:
                raise ValueError("constituents disagree with gross dated coverage")
        except ValueError as exc:
            plan["unreconstructable"].append({
                "trade_id": trade_id, "date": row_day,
                "budget": _budget_repr(budget), "reason": str(exc),
            })
            continue

        plan["delete"].append({
            "trade_id": trade_id, "date": row_day,
            "reason": "exclusive exact execution identities and quantities proven",
            "breakdown": sorted(
                ({"date": day, "qty": qty * sign} for (_, day, sign), qty in dated.items()),
                key=lambda item: (item["date"], item["qty"]),
            ),
        })
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
