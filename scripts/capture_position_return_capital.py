#!/usr/bin/env python3
"""Reconcile execution-linked return capital from persisted IB evidence.

The default is a read-only preview. ``--apply`` appends deterministic ledger
events and isolated margin observations. No IB request, what-if calculation,
Reg-T model, order placement, or structure-key backfill occurs here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from position_return_capital import (  # noqa: E402
    group_execution_transactions,
    replay_transactions,
    validate_margin_window,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _row_value(row: Any, index: int, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return row[index]


def _load_executions(db: Any) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT payload FROM position_execution_facts
           ORDER BY filled_at ASC, account_id ASC, exec_id ASC, revision ASC"""
    ).fetchall()
    latest: dict[tuple[str, str], tuple[tuple[int, str], dict[str, Any]]] = {}
    for row in rows or []:
        try:
            payload = json.loads(_row_value(row, 0, "payload"))
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        exec_id = str(payload.get("exec_id") or payload.get("execId") or "")
        account_id = str(payload.get("account_id") or payload.get("acctNumber") or "")
        root, separator, suffix = exec_id.rpartition(".")
        correction_root = root if separator else exec_id
        try:
            revision = int(suffix) if separator else 0
        except ValueError:
            revision = 0
        sort_key = (revision, exec_id)
        identity = (account_id, correction_root)
        canonical_payload = dict(payload)
        canonical_payload["source_exec_id"] = exec_id
        canonical_payload["exec_id"] = correction_root
        canonical_payload["revision"] = revision
        if identity not in latest or sort_key > latest[identity][0]:
            latest[identity] = (sort_key, canonical_payload)
    return [item[1] for item in sorted(latest.values(), key=lambda item: item[0][1])]


def _load_samples(db: Any) -> dict[str, list[dict[str, Any]]]:
    rows = db.execute(
        """SELECT sample_id, account_id, observed_from, observed_through, initial_margin,
                  maintenance_margin, currency, positions
           FROM account_margin_samples
           ORDER BY account_id ASC, observed_from ASC, observed_through ASC, sample_id ASC"""
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows or []:
        try:
            positions = json.loads(_row_value(row, 7, "positions") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        item = {
            "sample_id": str(_row_value(row, 0, "sample_id") or ""),
            "account_id": str(_row_value(row, 1, "account_id") or ""),
            "observed_from": str(_row_value(row, 2, "observed_from") or ""),
            "observed_through": str(_row_value(row, 3, "observed_through") or ""),
            "initial_margin": _row_value(row, 4, "initial_margin"),
            "maintenance_margin": _row_value(row, 5, "maintenance_margin"),
            "currency": str(_row_value(row, 6, "currency") or ""),
            "positions": positions,
        }
        grouped.setdefault(item["account_id"], []).append(item)
    return grouped


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _bracketing_samples(
    samples: list[dict[str, Any]],
    executions: list[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    first = min(_epoch(item["filled_at"]) for item in executions)
    last = max(_epoch(item["filled_at"]) for item in executions)
    before = [sample for sample in samples if _epoch(sample["observed_through"]) <= first]
    after = [sample for sample in samples if _epoch(sample["observed_from"]) >= last]
    return (before[-1] if before else None, after[0] if after else None)


def build_reconciliation_plan(db: Any, *, max_window_seconds: int = 120) -> dict[str, Any]:
    executions = _load_executions(db)
    transactions = group_execution_transactions(executions)
    replay = replay_transactions(transactions)
    samples_by_account = _load_samples(db)
    normalized_execs = [item for group in transactions for item in group]
    capital_by_instance: dict[str, float] = {}
    observations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for event in replay["events"]:
        account_id = event["account_id"]
        before, after = _bracketing_samples(
            samples_by_account.get(account_id, []), event["executions"]
        )
        if before is None or after is None or before["sample_id"] == after["sample_id"]:
            rejected.append({"event_id": event["event_id"], "reason": "missing-distinct-margin-samples"})
            continue
        start = _epoch(before["observed_from"])
        end = _epoch(after["observed_through"])
        window_execs = [
            item for item in normalized_execs
            if item["account_id"] == account_id and start <= _epoch(item["filled_at"]) <= end
        ]
        isolation = validate_margin_window(
            event,
            before,
            after,
            executions_in_window=window_execs,
            max_window_seconds=max_window_seconds,
        )
        if not isolation.get("valid"):
            rejected.append({"event_id": event["event_id"], "reason": isolation.get("reason")})
            continue

        previous = capital_by_instance.get(event["instance_id"])
        delta = float(isolation["delta_amount"])
        if event["kind"] == "OPEN":
            amount = delta
        elif event["kind"] in {"ADD", "REDUCE"} and previous is not None:
            amount = previous + delta
        elif event["kind"] == "CLOSE" and previous is not None:
            amount = 0.0
        else:
            rejected.append({"event_id": event["event_id"], "reason": "missing-prior-capital"})
            continue
        if amount < 0 or (event["kind"] != "CLOSE" and amount <= 0):
            rejected.append({"event_id": event["event_id"], "reason": "invalid-cumulative-capital"})
            continue
        capital_by_instance[event["instance_id"]] = amount

        execs = event["executions"]
        currencies = {item["currency"] for item in execs}
        if len(currencies) != 1 or next(iter(currencies)) != isolation["currency"]:
            rejected.append({"event_id": event["event_id"], "reason": "execution-currency-mismatch"})
            continue
        evidence = {
            "observed_at": after["observed_through"],
            "before_sample_id": before["sample_id"],
            "after_sample_id": after["sample_id"],
            "window_seconds": isolation["window_seconds"],
            "exec_ids": sorted({item["exec_id"] for item in execs}),
            "perm_ids": sorted({item["perm_id"] for item in execs if item.get("perm_id")}),
            "order_refs": sorted({item["order_ref"] for item in execs if item.get("order_ref")}),
            "multipliers": {str(item["con_id"]): item["multiplier"] for item in execs},
        }
        observation_identity = {
            "instance_id": event["instance_id"],
            "through_event_id": event["event_id"],
            "amount": amount,
            "delta": delta,
            "before": before["sample_id"],
            "after": after["sample_id"],
        }
        observations.append({
            "observation_id": f"obs-{_sha(observation_identity)[:24]}",
            "instance_id": event["instance_id"],
            "through_event_id": event["event_id"],
            "amount": amount,
            "delta_amount": delta,
            "before_sample_id": before["sample_id"],
            "after_sample_id": after["sample_id"],
            "currency": isolation["currency"],
            "evidence": evidence,
            "idempotency_key": _sha(observation_identity),
        })

    return {
        "executions": len(executions),
        "transactions": len(transactions),
        "instances": replay["instances"],
        "events": replay["events"],
        "observations": observations,
        "rejected": rejected,
        "errors": replay["errors"],
    }


def apply_reconciliation_plan(db: Any, plan: dict[str, Any]) -> dict[str, int]:
    """Atomically project the canonical execution revisions into the ledger."""
    stamp = _now_iso()
    try:
        db.execute("BEGIN")
        for instance in plan["instances"]:
            opening_event = next(
                event for event in plan["events"]
                if event["instance_id"] == instance["instance_id"] and event["kind"] == "OPEN"
            )
            db.execute(
                """INSERT OR IGNORE INTO position_instances
                   (instance_id, account_id, strategy_key, episode, opened_at,
                    opening_exec_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    instance["instance_id"], instance["account_id"], instance["strategy_key"],
                    instance["episode"], instance["opened_at"],
                    opening_event["executions"][0]["exec_id"], stamp,
                ),
            )
        for event in plan["events"]:
            event_payload = {
                "event_id": event["event_id"],
                "before_legs": event["before_legs"],
                "after_legs": event["after_legs"],
                "executions": [
                    {
                        "exec_id": item["exec_id"],
                        "revision": item.get("revision", 0),
                        "quantity": item["signed_quantity"],
                        "price": item["price"],
                    }
                    for item in event["executions"]
                ],
            }
            db.execute(
                """INSERT INTO position_instance_events
                   (event_id, instance_id, kind, transaction_key, effective_at,
                    before_legs, after_legs, exec_ids, source_digest, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET
                     effective_at=excluded.effective_at,
                     before_legs=excluded.before_legs,
                     after_legs=excluded.after_legs,
                     exec_ids=excluded.exec_ids,
                     source_digest=excluded.source_digest,
                     recorded_at=excluded.recorded_at""",
                (
                    event["event_id"], event["instance_id"], event["kind"],
                    event["transaction_key"], event["effective_at"],
                    _canonical(event["before_legs"]), _canonical(event["after_legs"]),
                    _canonical([item["exec_id"] for item in event["executions"]]),
                    _sha(event_payload), stamp,
                ),
            )
            for item in event["executions"]:
                revision = int(item.get("revision") or 0)
                prior = db.execute(
                    """SELECT revision, price FROM position_event_executions
                       WHERE event_id=? AND account_id=? AND exec_id=?""",
                    (event["event_id"], item["account_id"], item["exec_id"]),
                ).fetchall()
                if any(
                    int(_row_value(row, 0, "revision") or 0) != revision
                    or float(_row_value(row, 1, "price") or 0) != float(item["price"])
                    for row in prior or []
                ):
                    db.execute(
                        """UPDATE position_capital_observations
                           SET status='VOID', amount=NULL
                           WHERE through_event_id=? AND status='VALID'""",
                        (event["event_id"],),
                    )
                db.execute(
                    """DELETE FROM position_event_executions
                       WHERE event_id=? AND account_id=? AND exec_id=?""",
                    (event["event_id"], item["account_id"], item["exec_id"]),
                )
                db.execute(
                    """INSERT INTO position_event_executions
                       (event_id, account_id, exec_id, revision, con_id, perm_id,
                        order_ref, signed_quantity, price, multiplier, currency, filled_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event["event_id"], item["account_id"], item["exec_id"], revision,
                        item["con_id"], item["perm_id"], item["order_ref"] or None,
                        item["signed_quantity"], item["price"], item["multiplier"],
                        item["currency"], item["filled_at"],
                    ),
                )
        for observation in plan["observations"]:
            db.execute(
                """INSERT OR IGNORE INTO position_capital_observations
                   (observation_id, instance_id, through_event_id, status, method,
                    quality, amount, delta_amount, before_sample_id, after_sample_id,
                    currency, source, evidence, idempotency_key, recorded_at)
                   VALUES (?, ?, ?, 'VALID', 'OBSERVED_INIT_MARGIN_DELTA',
                           'observed', ?, ?, ?, ?, ?, 'ib-account-values', ?, ?, ?)""",
                (
                    observation["observation_id"], observation["instance_id"],
                    observation["through_event_id"], observation["amount"],
                    observation["delta_amount"], observation["before_sample_id"],
                    observation["after_sample_id"], observation["currency"],
                    _canonical(observation["evidence"]), observation["idempotency_key"], stamp,
                ),
            )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {
        "instances": len(plan["instances"]),
        "events": len(plan["events"]),
        "observations": len(plan["observations"]),
    }


def reconcile_position_return_capital(db: Any, *, apply: bool = False) -> dict[str, Any]:
    plan = build_reconciliation_plan(db)
    result = {
        "status": "applied" if apply else "preview",
        "executions": plan["executions"],
        "transactions": plan["transactions"],
        "instances": len(plan["instances"]),
        "events": len(plan["events"]),
        "observations": len(plan["observations"]),
        "rejected": plan["rejected"],
        "errors": plan["errors"],
    }
    if apply:
        result["written"] = apply_reconciliation_plan(db, plan)
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Append the deterministic plan")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        from db.client import get_db
        result = reconcile_position_return_capital(get_db(), apply=args.apply)
    except Exception as exc:  # noqa: BLE001
        result = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"return-capital reconcile failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
