#!/usr/bin/env python3
"""Rebuild ``gross_fill_breakdown`` on legacy Flex aggregate journal rows.

``scripts.cleanup_legacy_flex_aggregates`` cannot repair a legacy aggregate
from the journal: its ``+``-joined ``ib_exec_id`` holds Flex tradeIDs, while
individual real-time rows carry IB API execIds (REL-275). This tool restores
the dated gross coverage from the authoritative source instead: saved Flex
trade statements, execution level. No Flex Web Service call is made.

An aggregate is stamped only when EVERY tradeID part is found exactly once in
the supplied statements (after dropping superseded corrections, as
``journal_rehydrate`` does), no other aggregate claims it, the contract
matches, and the executions reproduce what the row records (action, quantity,
round-trip quantity, net ``fill_breakdown``, first-fill date). Everything
else is refused with a reason. Aggregates with no part in the statements are
reported as out of statement period. Never guesses, never partially stamps,
never deletes.

DRY RUN BY DEFAULT. ``--apply`` writes only ``gross_fill_breakdown``.

    python -m scripts.rebuild_flex_gross_breakdown --xml trades.xml            # dry run
    python -m scripts.rebuild_flex_gross_breakdown --xml trades.xml --apply    # operator only
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
import math
import pathlib
import sys
from typing import Any, Iterable, Optional

try:  # package import (python -m scripts.…)
    from scripts import journal_rehydrate as rehydrate
    from scripts.cleanup_legacy_flex_aggregates import _connect, _payload, _parts, is_aggregate
except ImportError:  # direct script execution
    import journal_rehydrate as rehydrate  # type: ignore[no-redef]
    from cleanup_legacy_flex_aggregates import (  # type: ignore[no-redef]
        _connect, _payload, _parts, is_aggregate,
    )

# journal_rehydrate has put scripts/ and scripts/trade_blotter/ on sys.path.
from clients.journal_basis import _bucket_key, _normalize_ticker  # noqa: E402

FIELD = "gross_fill_breakdown"


def load_statement_executions(xml_texts: Iterable[str]) -> list[Any]:
    """Parse saved Flex trade statements into Execution objects (all files)."""
    from lib.flex_classify import TRADES, FlexClassifyError, classify_flex_xml
    from trade_blotter.flex_query import FlexQueryFetcher

    executions: list[Any] = []
    for index, xml_text in enumerate(xml_texts):
        try:
            kind = classify_flex_xml(xml_text)
        except FlexClassifyError as exc:
            raise SystemExit(f"statement #{index + 1}: {exc}") from None
        if kind != TRADES:
            raise SystemExit(f"statement #{index + 1}: not_trade_statement:{kind}")
        parsed, dropped = FlexQueryFetcher(token="x", query_id="x").parse_xml_with_drops(xml_text)
        if dropped:
            raise SystemExit(f"statement #{index + 1}: {dropped} Trade rows failed to parse")
        executions.extend(parsed)
    return executions


def _day(exec_obj: Any) -> str:
    # Same session dating as journal_rehydrate._bucket_to_entry.
    return exec_obj.time.strftime("%Y-%m-%d")


def _signed(exec_obj: Any) -> Decimal:
    return exec_obj.quantity if exec_obj.side.value == "BOT" else -exec_obj.quantity


def _num(value: Decimal) -> Any:
    return int(value) if value == value.to_integral_value() else float(value)


def _contract_matches(payload: dict[str, Any], exec_obj: Any) -> bool:
    if _normalize_ticker(payload.get("ticker") or payload.get("symbol")) != _normalize_ticker(exec_obj.symbol):
        return False
    row_key = _bucket_key(payload)
    if exec_obj.sec_type.value == "STK":
        return row_key is None
    return row_key is not None and row_key == _bucket_key({
        "ticker": exec_obj.symbol, "strike": exec_obj.strike,
        "right": exec_obj.right, "expiry": exec_obj.expiry,
    })


def _check_totals(payload: dict[str, Any], group: list[Any]) -> None:
    disagree = ValueError("statement executions disagree with the row's recorded totals")
    buys = sum((e.quantity for e in group if e.side.value == "BOT"), Decimal(0))
    sells = sum((e.quantity for e in group if e.side.value != "BOT"), Decimal(0))
    net = buys - sells
    try:
        qty = Decimal(str(abs(float(payload.get("contracts") or payload.get("shares") or 0))))
    except (TypeError, ValueError):
        raise disagree from None
    action = str(payload.get("action") or "").strip().upper()
    if action == "CLOSED":
        ok = net == 0 and qty == buys
    elif action.startswith("BUY"):
        ok = net > 0 and qty == net
    elif action.startswith("SELL"):
        ok = net < 0 and qty == -net
    else:
        ok = False
    if not ok:
        raise disagree
    if payload.get("total_round_trip_quantity") is not None:
        try:
            if Decimal(str(float(payload["total_round_trip_quantity"]))) != max(buys, sells):
                raise disagree
        except (TypeError, ValueError):
            raise disagree from None
    if str(payload.get("date") or "")[:10] != min(_day(e) for e in group):
        raise disagree
    breakdown = payload.get("fill_breakdown")
    if breakdown:
        per_day: Counter = Counter()
        for e in group:
            per_day[_day(e)] += _signed(e)
        expected = {day: q for day, q in per_day.items() if q != 0}
        try:
            recorded: Counter = Counter()
            for item in breakdown:
                recorded[str(item["date"])[:10]] += Decimal(str(float(item["qty"])))
        except (KeyError, TypeError, ValueError):
            raise disagree from None
        if {d: q for d, q in recorded.items() if q != 0} != expected:
            raise disagree


def plan_rebuild(rows: Iterable[tuple[str, Any]], executions: list[Any]) -> dict[str, Any]:
    """Plan gross_fill_breakdown stamps; refuse anything not exactly proven."""
    survivors = rehydrate._drop_superseded_executions(executions)
    surviving_ids = {id(e) for e in survivors}
    by_id: dict[str, list[Any]] = defaultdict(list)
    superseded: set[str] = set()
    for e in executions:
        if id(e) in surviving_ids:
            by_id[str(e.exec_id)].append(e)
        else:
            superseded.add(str(e.exec_id))
    all_ids = set(by_id) | superseded
    days = sorted(_day(e) for e in executions)

    parsed = [(trade_id, _payload(raw)) for trade_id, raw in rows]
    aggregates = [item for item in parsed if is_aggregate(item[1])]
    claims: Counter = Counter()
    for _, payload in aggregates:
        claims.update(set(_parts(payload)))

    plan: dict[str, Any] = {
        "stamp": [], "refuse": [], "out_of_period": [], "already_stamped": 0,
        "coverage": (days[0], days[-1]) if days else None,
    }
    for trade_id, payload in aggregates:
        if FIELD in payload:
            plan["already_stamped"] += 1
            continue
        row_day = str(payload.get("date") or "")[:10]
        parts = _parts(payload)
        if all(part and part not in all_ids for part in parts):
            plan["out_of_period"].append({"trade_id": trade_id, "date": row_day})
            continue
        try:
            if not all(parts) or len(parts) != len(set(parts)):
                raise ValueError("ib_exec_id has empty or repeated parts")
            if any(part in superseded for part in parts):
                raise ValueError("part names a superseded (corrected) execution")
            if any(part not in by_id for part in parts):
                raise ValueError("part missing from supplied statements")
            if any(len(by_id[part]) != 1 for part in parts):
                raise ValueError("part not found exactly once in supplied statements")
            if any(claims[part] != 1 for part in parts):
                raise ValueError("part claimed by more than one aggregate")
            group = [by_id[part][0] for part in parts]
            if not all(_contract_matches(payload, e) for e in group):
                raise ValueError("execution contract does not match row contract")
            _check_totals(payload, group)
        except ValueError as exc:
            plan["refuse"].append({"trade_id": trade_id, "date": row_day, "reason": str(exc)})
            continue
        gross: Counter = Counter()
        for e in group:
            signed = _signed(e)
            gross[_day(e), 1 if signed > 0 else -1] += signed
        plan["stamp"].append({
            "trade_id": trade_id, "date": row_day,
            FIELD: sorted(
                ({"date": day, "qty": _num(qty)} for (day, _), qty in gross.items() if qty != 0),
                key=lambda item: (item["date"], item["qty"]),
            ),
        })
    return plan


def render_plan(plan: dict[str, Any]) -> str:
    coverage = plan["coverage"]
    lines = [
        f"statement executions cover: {coverage[0]} .. {coverage[1]}" if coverage
        else "statement executions cover: (none)",
        f"already stamped: {plan['already_stamped']}",
        f"stamp: {len(plan['stamp'])}",
    ]
    for item in plan["stamp"]:
        lines.append(f"  SET {FIELD} {item['trade_id']}  date={item['date']}  {json.dumps(item[FIELD])}")
    lines.append(f"out of statement period: {len(plan['out_of_period'])}")
    for item in plan["out_of_period"]:
        lines.append(f"  OUT {item['trade_id']}  date={item['date']}")
    lines.append(f"refused: {len(plan['refuse'])}")
    for reason, count in sorted(Counter(item["reason"] for item in plan["refuse"]).items()):
        lines.append(f"  {count:>4}  {reason}")
    for item in plan["refuse"]:
        lines.append(f"  REFUSE {item['trade_id']}  date={item['date']}  {item['reason']}")
    return "\n".join(lines)


def _apply(db: Any, plan: dict[str, Any]) -> int:
    """Stamp every planned row in one transaction; roll back on any conflict."""
    try:
        for item in plan["stamp"]:
            current = db.execute(
                "SELECT payload FROM journal WHERE trade_id = ?", (item["trade_id"],)
            ).fetchall()
            if len(current) != 1:
                raise RuntimeError(f"{item['trade_id']}: row vanished")
            raw = current[0][0]
            payload = _payload(raw)
            if FIELD in payload:
                raise RuntimeError(f"{item['trade_id']}: row gained {FIELD} since planning")
            payload[FIELD] = item[FIELD]
            cursor = db.execute(
                "UPDATE journal SET payload = ? WHERE trade_id = ? AND payload = ?",
                (json.dumps(payload), item["trade_id"], raw),
            )
            if getattr(cursor, "rowcount", 1) != 1:
                raise RuntimeError(f"{item['trade_id']}: guarded update matched no row")
        db.commit()
    except Exception:
        db.rollback()
        raise
    verified = 0
    for item in plan["stamp"]:
        row = db.execute("SELECT payload FROM journal WHERE trade_id = ?", (item["trade_id"],)).fetchall()
        if row and _payload(row[0][0]).get(FIELD) == item[FIELD]:
            verified += 1
    return verified


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xml", nargs="+", required=True, metavar="PATH",
                        help="saved Flex trade statement(s), execution level")
    parser.add_argument("--apply", action="store_true", help=f"write {FIELD} on planned rows")
    args = parser.parse_args(argv)

    executions = load_statement_executions(pathlib.Path(p).read_text() for p in args.xml)
    db = _connect()
    rows = db.execute("SELECT trade_id, payload FROM journal").fetchall()
    plan = plan_rebuild([(row[0], row[1]) for row in rows], executions)

    print(f"journal rows scanned: {len(rows)}; statement executions: {len(executions)}")
    print(render_plan(plan))
    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to stamp.")
        return 0
    verified = _apply(db, plan)
    print(f"\nAPPLIED: stamped {len(plan['stamp'])}, verified {verified}.")
    return 0 if verified == len(plan["stamp"]) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
