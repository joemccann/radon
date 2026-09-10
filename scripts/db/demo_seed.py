#!/usr/bin/env python3
"""Idempotent seeder for the demo.radon.run Turso DB.

Two jobs:
  (a) apply scripts/db/demo_migrations/*.sql (demo_users, demo_ai_usage) in
      lex order to the DEMO Turso DB, and
  (b) write a synthetic, fabricated-but-consistent dataset into the
      prod-shaped global tables (portfolio_snapshots, journal, open_orders)
      so the demo terminal renders a believable account with ZERO real data.

⛔ HARD GUARD: refuses to run if the target URL looks like production
(contains 'radon-joemccann'). The demo DB is a SEPARATE Turso database
(TURSO_DEMO_DB_URL / TURSO_DEMO_AUTH_TOKEN), never the prod TURSO_DB_URL.

The numbers are modeled on marketing-mockups/portfolio-recreation.html and
reconcile exactly:
    market_value  = last_price  * contracts * 100   (options)
    initial_value = entry_price * contracts * 100
    pnl           = market_value - initial_value

Idempotent: migrations are INSERT-OR-IGNORE versioned; the snapshot/journal/
order rows use fixed primary keys so re-running overwrites rather than
duplicates.

Usage:
    TURSO_DEMO_DB_URL=libsql://radon-demo-xxx.turso.io \\
    TURSO_DEMO_AUTH_TOKEN=... python3 scripts/db/demo_seed.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
_DEMO_MIGRATIONS_DIR = Path(__file__).resolve().parent / "demo_migrations"

# A target URL containing this substring is the production database. The demo
# DB is provisioned under a distinct name (radon-demo-*); seeding prod is a
# data-loss / leak event, so we refuse outright.
_PROD_URL_MARKER = "radon-joemccann"

# ...and the demo DB is provisioned under this name. Requiring the POSITIVE
# marker as well means a mistyped / unrelated target is refused rather than
# silently written: the same ~/radon-cloud/.env on the prod app host holds both
# credential pairs, so "not prod-marked" alone is too weak a guard.
_DEMO_URL_MARKER = "radon-demo"

# Seed dates are ANCHORED to the run date, never hardcoded. The first seed
# baked 2026-06 expiries in, so by September every synthetic option had expired
# and the demo rendered a dead account. Re-running the seeder now always
# produces a live-looking book. Override the anchor for deterministic tests.
_SEED_TODAY_ENV = "RADON_DEMO_SEED_TODAY"
_SEED_SNAPSHOT_TIME = "T13:42:11+00:00"


def _seed_anchor() -> date:
    override = os.environ.get(_SEED_TODAY_ENV)
    return date.fromisoformat(override) if override else date.today()


def seed_snapshot_taken_at() -> str:
    return f"{_seed_anchor().isoformat()}{_SEED_SNAPSHOT_TIME}"


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    # weekday(): Monday=0 … Friday=4
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def _expiry_months_out(months: int) -> date:
    """Third Friday `months` ahead, rolled forward if it has already passed."""
    anchor = _seed_anchor()
    year, month = anchor.year, anchor.month + months
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    expiry = _third_friday(year, month)
    while expiry <= anchor:
        month += 1
        year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
        expiry = _third_friday(year, month)
    return expiry


def _expiry_iso(months: int) -> str:
    return _expiry_months_out(months).isoformat()


def _expiry_compact(months: int) -> str:
    return _expiry_months_out(months).strftime("%Y%m%d")


def _days_ago(days: int) -> str:
    return (_seed_anchor() - timedelta(days=days)).isoformat()

# The demo series gets its OWN, NAME-keyed ledger.
#
# The demo Turso DB is a full prod-shaped clone and shares one
# `schema_migrations` table with the MAIN migration series (versions 1..69).
# demo_migrations/0003 declared version 3, which the main series had claimed on
# 2026-06-29, so a version-keyed applier read it as already applied and skipped
# `demo_webhook_events` forever — every Clerk user.created webhook then threw
# and demo.radon.run provisioned no trial for 22 days. Keying on the file NAME
# in a dedicated table makes a version collision with the main series
# structurally impossible.
_DEMO_LEDGER_SQL = """
    CREATE TABLE IF NOT EXISTS demo_schema_migrations (
      name       TEXT PRIMARY KEY,
      applied_at TEXT NOT NULL
    )
    """


def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]

        load_dotenv(_PROJECT_DIR / ".env")
        load_dotenv(_PROJECT_DIR / "web" / ".env")
    except Exception:
        pass


def _read_demo_env() -> tuple[str, str]:
    url = os.environ.get("TURSO_DEMO_DB_URL")
    token = os.environ.get("TURSO_DEMO_AUTH_TOKEN")
    if not url or not token:
        sys.stderr.write(
            "TURSO_DEMO_DB_URL and TURSO_DEMO_AUTH_TOKEN must be set "
            "(the SEPARATE demo Turso DB, not the prod TURSO_DB_URL).\n"
        )
        sys.exit(2)
    return url, token


def assert_not_prod(url: str) -> None:
    """Hard guard — abort unless the target URL is provably the demo DB."""
    if _PROD_URL_MARKER in url:
        raise SystemExit(
            "REFUSING TO SEED: target URL "
            f"({url!r}) contains the production marker {_PROD_URL_MARKER!r}. "
            "demo_seed.py writes ONLY to the separate demo Turso DB."
        )
    if _DEMO_URL_MARKER not in url:
        raise SystemExit(
            "REFUSING TO SEED: target URL "
            f"({url!r}) does not carry the demo marker {_DEMO_URL_MARKER!r}. "
            "demo_seed.py writes ONLY to the separate demo Turso DB."
        )


def _split_statements(sql: str) -> list[str]:
    """Strip line comments, split on `;` — mirrors scripts/db/migrate.py."""
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    parts = re.split(r";\s*$", stripped, flags=re.MULTILINE)
    return [s.strip() for s in parts if s.strip()]


def _list_demo_migrations() -> list[tuple[int, str, Path]]:
    if not _DEMO_MIGRATIONS_DIR.is_dir():
        raise SystemExit(f"No demo migrations dir at {_DEMO_MIGRATIONS_DIR}")
    rows: list[tuple[int, str, Path]] = []
    pattern = re.compile(r"^(\d+)_.*\.sql$")
    for entry in sorted(_DEMO_MIGRATIONS_DIR.iterdir()):
        match = pattern.match(entry.name)
        if match:
            rows.append((int(match.group(1)), entry.name, entry))
    return rows


def apply_demo_migrations(db) -> None:
    """Apply pending demo migrations, keyed by FILE NAME (idempotent).

    Never reads or writes the shared `schema_migrations` table — see
    _DEMO_LEDGER_SQL for why that ledger cannot be trusted for this series.
    """
    db.execute(_DEMO_LEDGER_SQL)
    db.commit()
    applied = {
        row[0]
        for row in db.execute("SELECT name FROM demo_schema_migrations").fetchall()
    }
    for _version, name, path in _list_demo_migrations():
        if name in applied:
            continue
        print(f"[demo-seed] applying migration {name}")
        for stmt in _split_statements(path.read_text(encoding="utf-8")):
            db.execute(stmt)
        db.execute(
            "INSERT OR REPLACE INTO demo_schema_migrations (name, applied_at) "
            "VALUES (?, datetime('now'))",
            (name,),
        )
        db.commit()


# ── synthetic dataset ────────────────────────────────────────────────────


def _leg(direction, type_, strike, entry_per_contract, last_per_contract, contracts):
    """One option leg with reconciling MV / initial-value math.

    `entry_per_contract` / `last_per_contract` are per-CONTRACT dollar values
    (already × 100 for options), matching PortfolioLeg.avg_cost convention.
    """
    initial = entry_per_contract * contracts
    market_value = last_per_contract * contracts
    return {
        "direction": direction,
        "contracts": contracts,
        "type": type_,
        "strike": strike,
        "entry_cost": round(initial, 2),
        "avg_cost": round(entry_per_contract, 2),
        "market_price": round(last_per_contract / 100, 4),
        "market_value": round(market_value, 2),
    }


def _single_leg_position(
    pos_id, ticker, structure, expiry, direction, strike, type_,
    entry_share, last_share, contracts, entry_date,
):
    """A single-leg long option position. share-priced inputs → per-contract."""
    entry_per_contract = entry_share * 100
    last_per_contract = last_share * 100
    leg = _leg(direction, type_, strike, entry_per_contract, last_per_contract, contracts)
    initial = leg["entry_cost"]
    market_value = leg["market_value"]
    return {
        "id": pos_id,
        "ticker": ticker,
        "structure": structure,
        "structure_type": "Long Option",
        "risk_profile": "defined",
        "expiry": expiry,
        "contracts": contracts,
        "direction": direction,
        "entry_cost": initial,
        "max_risk": initial,
        "market_value": market_value,
        "legs": [leg],
        "kelly_optimal": None,
        "target": None,
        "stop": None,
        "entry_date": entry_date,
    }


def _build_positions() -> list[dict]:
    """Four synthetic defined-risk positions modeled on the mockup."""
    # SPY/QQQ/TSLA per the Phase-1 brief, plus a SPY bull call spread.
    positions = [
        _single_leg_position(
            1, "SPY", "Long Call $600.0", _expiry_iso(1), "LONG", 600.0, "Call",
            entry_share=8.40, last_share=13.35, contracts=20, entry_date=_days_ago(20),
        ),
        _single_leg_position(
            2, "QQQ", "Long Call $520.0", _expiry_iso(2), "LONG", 520.0, "Call",
            entry_share=6.10, last_share=9.85, contracts=15, entry_date=_days_ago(33),
        ),
        _single_leg_position(
            3, "TSLA", "Long Call $340.0", _expiry_iso(3), "LONG", 340.0, "Call",
            entry_share=11.85, last_share=18.40, contracts=10, entry_date=_days_ago(30),
        ),
    ]

    # SPY bull call spread (defined-risk combo): long 620 call, short 650 call.
    long_leg = _leg("LONG", "Call", 620.0, 22.40 * 100, 30.20 * 100, 20)
    short_leg = _leg("SHORT", "Call", 650.0, -11.20 * 100, -18.60 * 100, 20)
    spread_initial = long_leg["entry_cost"] + short_leg["entry_cost"]
    spread_mv = long_leg["market_value"] + short_leg["market_value"]
    positions.append(
        {
            "id": 4,
            "ticker": "SPY",
            "structure": "Bull Call Spread $620.0/$650.0",
            "structure_type": "Bull Call Spread",
            "risk_profile": "defined",
            "expiry": _expiry_iso(1),
            "contracts": 20,
            "direction": "DEBIT",
            "entry_cost": round(spread_initial, 2),
            "max_risk": round(spread_initial, 2),
            "market_value": round(spread_mv, 2),
            "legs": [long_leg, short_leg],
            "kelly_optimal": None,
            "target": None,
            "stop": None,
            "entry_date": _days_ago(9),
        }
    )
    return positions


def build_portfolio_payload() -> dict:
    """Synthetic portfolio snapshot, prod-shaped (web/lib/types PortfolioData)."""
    positions = _build_positions()
    total_initial = sum(p["entry_cost"] for p in positions)
    total_mv = sum(p["market_value"] for p in positions)
    unrealized = round(total_mv - total_initial, 2)
    net_liq = 502_000.00  # ~500K demo net liq

    account_summary = {
        "net_liquidation": net_liq,
        "daily_pnl": 4442.0,
        "unrealized_pnl": unrealized,
        "realized_pnl": 60785.0,
        "settled_cash": round(net_liq - total_mv, 2),
        "maintenance_margin": 0.0,
        "excess_liquidity": round(net_liq - total_mv, 2),
        "buying_power": round((net_liq - total_mv) * 2, 2),
        "dividends": 0.0,
        "cash": round(net_liq - total_mv, 2),
        "gross_position_value": round(total_mv, 2),
    }

    return {
        "bankroll": net_liq,
        "peak_value": net_liq,
        "last_sync": seed_snapshot_taken_at(),
        "positions": positions,
        "total_deployed_pct": round(total_initial / net_liq * 100, 2),
        "total_deployed_dollars": round(total_initial, 2),
        "remaining_capacity_pct": round(100 - total_initial / net_liq * 100, 2),
        "position_count": len(positions),
        "defined_risk_count": len(positions),
        "undefined_risk_count": 0,
        "avg_kelly_optimal": None,
        "account_summary": account_summary,
        "demo": True,
    }


def build_journal_rows() -> list[tuple[str, dict, str]]:
    """Synthetic opening journal rows matching the seeded positions.

    Returns (trade_id, payload, filled_at). One opener per position.
    """
    rows = [
        ("demo-spy-call-open", "SPY", "Long Call $600.0", "BUY_OPTION", 600.0,
         "C", _expiry_iso(1), 20, 8.40, _days_ago(20)),
        ("demo-qqq-call-open", "QQQ", "Long Call $520.0", "BUY_OPTION", 520.0,
         "C", _expiry_iso(2), 15, 6.10, _days_ago(33)),
        ("demo-tsla-call-open", "TSLA", "Long Call $340.0", "BUY_OPTION", 340.0,
         "C", _expiry_iso(3), 10, 11.85, _days_ago(30)),
        ("demo-spy-bcs-open", "SPY", "Bull Call Spread $620.0/$650.0", "BUY_COMBO",
         620.0, "C", _expiry_iso(1), 20, 11.20, _days_ago(9)),
    ]
    out: list[tuple[str, dict, str]] = []
    for trade_id, ticker, structure, action, strike, right, expiry, contracts, fill_share, date in rows:
        filled_at = date
        payload = {
            "ticker": ticker,
            "symbol": ticker,
            "structure": structure,
            "action": action,
            "decision": "EXECUTED",
            "strike": strike,
            "right": right,
            "expiry": expiry,
            "contracts": contracts,
            "fill_price": fill_share,
            "total_cost": round(fill_share * contracts * 100, 2),
            "cost_basis": round(fill_share * contracts * 100, 2),
            "date": date,
            "filled_at": filled_at,
            "demo": True,
            "notes": "Synthetic demo seed.",
        }
        out.append((trade_id, payload, filled_at))
    return out


def build_open_orders() -> list[tuple[int, dict]]:
    """A couple of synthetic working orders (open_orders, keyed by perm_id)."""
    return [
        (
            900000001,
            {
                "orderId": 9001,
                "permId": 900000001,
                "symbol": "TSLA",
                "action": "BUY",
                "orderType": "LMT",
                "totalQuantity": 5,
                "limitPrice": 12.50,
                "auxPrice": None,
                "status": "Submitted",
                "filled": 0,
                "remaining": 5,
                "avgFillPrice": None,
                "tif": "DAY",
                "contract": {
                    "conId": None, "symbol": "TSLA", "secType": "OPT",
                    "strike": 360.0, "right": "C", "expiry": _expiry_compact(3),
                },
                "demo": True,
            },
        ),
        (
            900000002,
            {
                "orderId": 9002,
                "permId": 900000002,
                "symbol": "QQQ",
                "action": "SELL",
                "orderType": "LMT",
                "totalQuantity": 15,
                "limitPrice": 14.00,
                "auxPrice": None,
                "status": "Submitted",
                "filled": 0,
                "remaining": 15,
                "avgFillPrice": None,
                "tif": "GTC",
                "contract": {
                    "conId": None, "symbol": "QQQ", "secType": "OPT",
                    "strike": 520.0, "right": "C", "expiry": _expiry_compact(2),
                },
                "demo": True,
            },
        ),
    ]


def seed_synthetic_dataset(db) -> None:
    """Write portfolio_snapshots / journal / open_orders synthetic rows.

    Fixed primary keys keep this idempotent (overwrite, not duplicate).
    """
    portfolio = build_portfolio_payload()
    # The snapshot key moves with the anchor, so drop the previous seed rather
    # than leaving a stale-dated row behind for the "latest snapshot" read.
    db.execute("DELETE FROM portfolio_snapshots")
    db.execute(
        "INSERT OR REPLACE INTO portfolio_snapshots (taken_at, payload) VALUES (?, ?)",
        (seed_snapshot_taken_at(), json.dumps(portfolio)),
    )

    for trade_id, payload, filled_at in build_journal_rows():
        db.execute(
            "INSERT OR REPLACE INTO journal (trade_id, payload, filled_at, written_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (trade_id, json.dumps(payload), filled_at),
        )

    for perm_id, payload in build_open_orders():
        db.execute(
            "INSERT OR REPLACE INTO open_orders (perm_id, payload, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (perm_id, json.dumps(payload)),
        )

    db.commit()
    print(
        f"[demo-seed] seeded {portfolio['position_count']} positions, "
        f"{len(build_journal_rows())} journal rows, "
        f"{len(build_open_orders())} open orders"
    )


def main() -> None:
    _load_env()
    url, token = _read_demo_env()
    assert_not_prod(url)

    try:
        import libsql_experimental as libsql  # type: ignore[import-untyped]
    except ImportError:
        sys.stderr.write(
            "libsql_experimental is not installed. pip install libsql-experimental\n"
        )
        sys.exit(1)

    print(f"[demo-seed] target → {url}")
    db = libsql.connect(url, auth_token=token)

    # The synthetic dataset lands in the prod-SHAPED tables, which the demo DB
    # gets from the regular migrations dir (applied by deploy). Bootstrap the
    # minimal global tables here so a fresh demo DB can be seeded standalone.
    _ensure_global_tables(db)
    apply_demo_migrations(db)
    seed_synthetic_dataset(db)
    print("[demo-seed] done")


def _ensure_global_tables(db) -> None:
    """Create the prod-shaped global tables the seed writes into, if absent.

    Mirrors the relevant CREATE TABLE statements from 0001_init.sql /
    0004_orders_and_ephemeral.sql so the seeder is self-contained on a brand
    new demo DB. IF NOT EXISTS keeps it harmless when the real migrations have
    already run.
    """
    db.execute(
        "CREATE TABLE IF NOT EXISTS portfolio_snapshots ("
        "taken_at TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS journal ("
        "trade_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
        "filled_at TEXT, written_at TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS open_orders ("
        "perm_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    db.commit()


if __name__ == "__main__":
    main()
