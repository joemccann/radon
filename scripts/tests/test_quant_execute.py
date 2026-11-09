"""Quant sleeve execution bridge: sizing, admission, holdings attribution,
the exact order payload on the wire, dry-run safety, migration 0110.

Every dependency is injected; nothing here touches IB or Turso.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
import quant_execute as qe

MIGRATIONS = Path(__file__).parents[1] / "db" / "migrations"


def _targets(rows, *, as_of="2026-11-06", rebalance=True):
    return {"as_of": as_of, "recorded_at": f"{as_of}T21:30:00Z", "rebalance_today": rebalance, "rows": rows}


def _row(symbol, weight, notional, close):
    return {"symbol": symbol, "weight": weight, "notional": notional, "close": close}


def _deps(*, targets=None, fills=(), prior=(), net_liq=None, placer=None, persist=None, heartbeat=None):
    calls = {"placed": [], "persisted": [], "health": []}

    def _placer(params):
        calls["placed"].append(params)
        return placer(params) if placer else {"status": "ok", "orderId": 7, "permId": 99}

    def _persist(key, result):
        snapshot = dict(result)
        snapshot["orders"] = [dict(o) for o in result.get("orders") or []]
        calls["persisted"].append((key, snapshot))

    deps = qe.Deps(
        load_targets=lambda key: targets,
        load_fills=lambda key: list(fills),
        prior_orders=lambda key, as_of: list(prior),
        net_liq=lambda: net_liq,
        placer=_placer,
        persist=persist or _persist,
        heartbeat=heartbeat or (lambda state, **kw: calls["health"].append(("quant-execute", state, kw))),
    )
    return deps, calls


TODAY = date(2026, 11, 7)


class TestSizing:
    def test_target_shares_floors_toward_zero_and_clamps_shorts(self):
        assert qe.target_shares(25_000.0, 501.3, allow_short=False) == 49
        assert qe.target_shares(-25_000.0, 501.3, allow_short=False) == 0
        assert qe.target_shares(-25_000.0, 501.3, allow_short=True) == -49
        assert qe.target_shares(None, 500.0, allow_short=True) is None
        assert qe.target_shares(1000.0, 0.0, allow_short=True) is None

    def test_limit_offsets_buy_up_sell_down(self):
        assert qe.limit_price(100.0, "BUY", 20) == 100.2
        assert qe.limit_price(100.0, "SELL", 20) == 99.8

    def test_order_ref_is_prefixed_and_fits_ib(self):
        assert qe.order_ref("inverse_vol") == "quant:inverse_vol"
        assert len(qe.order_ref("signal_change_defensive")) <= 32


class TestPlan:
    def test_buys_sells_dust_and_on_target(self):
        rows = [
            _row("SPY", 0.5, 50_000.0, 500.0),  # target 100, held 40  -> BUY 60
            _row("TLT", 0.3, 30_000.0, 100.0),  # target 300, held 350 -> SELL 50
            _row("GLD", 0.2, 20_000.0, 200.0),  # target 100, held 100 -> on target
            _row("IEF", 0.0, 0.0, 95.0),  # target 0, held 0     -> on target
        ]
        held = {"SPY": 40, "TLT": 350, "GLD": 100}
        plan = qe.plan_orders(rows, held, allow_short=False, offset_bps=20)
        by = {p["symbol"]: p for p in plan}
        assert (by["SPY"]["action"], by["SPY"]["quantity"], by["SPY"]["limit_price"]) == ("BUY", 60, 501.0)
        assert (by["TLT"]["action"], by["TLT"]["quantity"], by["TLT"]["limit_price"]) == ("SELL", 50, 99.8)
        assert by["GLD"]["skip"] == "on_target" and by["IEF"]["skip"] == "on_target"

    def test_dust_is_skipped(self):
        plan = qe.plan_orders([_row("SPY", 0.5, 50_000.0, 500.0)], {"SPY": 100.04}, allow_short=False, offset_bps=20)
        assert plan[0]["skip"] in ("dust", "on_target")
        plan = qe.plan_orders([_row("XYZ", 0.1, 1_000.0, 20.0)], {"XYZ": 49}, allow_short=False, offset_bps=20)
        assert plan[0]["skip"] == "dust"  # one share at $20 is under the $50 floor
        plan = qe.plan_orders([_row("SPY", 0.5, 50_020.0, 500.0)], {"SPY": 99}, allow_short=False, offset_bps=20)
        assert (plan[0]["action"], plan[0]["quantity"]) == ("BUY", 1)  # one share at $500 is a real trade

    def test_never_sells_below_flat_unless_short_allowed(self):
        rows = [_row("USO", -0.5, -50_000.0, 70.0)]
        long_only = qe.plan_orders(rows, {"USO": 30}, allow_short=False, offset_bps=20)[0]
        assert (long_only["target_shares"], long_only["action"], long_only["quantity"]) == (0, "SELL", 30)
        shorting = qe.plan_orders(rows, {"USO": 30}, allow_short=True, offset_bps=20)[0]
        assert (shorting["target_shares"], shorting["action"], shorting["quantity"]) == (-714, "SELL", 744)
        flat = qe.plan_orders(rows, {}, allow_short=False, offset_bps=20)[0]
        assert flat["skip"] == "on_target" and flat["action"] is None  # clamped target 0, held 0
        # held short (legacy) with shorts no longer allowed: the only sell-side
        # path that cannot act is a short that would need to grow further.
        deeper = qe.plan_orders([_row("USO", -0.5, -50_000.0, 70.0)], {"USO": -10}, allow_short=False, offset_bps=20)[0]
        assert (deeper["target_shares"], deeper["action"], deeper["quantity"]) == (0, "BUY", 10)

    def test_notional_falls_back_to_sleeve_nav_and_missing_price_skips(self):
        plan = qe.plan_orders(
            [_row("SPY", 0.25, None, 400.0)], {}, allow_short=False, offset_bps=20, sleeve_nav=100_000.0
        )
        assert (plan[0]["notional"], plan[0]["target_shares"], plan[0]["quantity"]) == (25_000.0, 62, 62)
        plan = qe.plan_orders([_row("SPY", 0.25, None, 400.0)], {}, allow_short=False, offset_bps=20)
        assert plan[0]["skip"] == "no_notional"
        plan = qe.plan_orders([_row("SPY", 0.25, 1000.0, None)], {}, allow_short=False, offset_bps=20)
        assert plan[0]["skip"] == "no_price"

    def test_symbol_that_left_the_universe_is_listed_for_flattening(self):
        plan = qe.plan_orders([_row("SPY", 1.0, 1000.0, 100.0)], {"EEM": 12}, allow_short=False, offset_bps=20)
        orphan = [p for p in plan if p["symbol"] == "EEM"][0]
        assert orphan["target_shares"] == 0 and orphan["skip"] == "no_price" and orphan["action"] is None


class TestHoldings:
    def test_sums_signed_stock_fills_and_ignores_options(self):
        fills = [
            {"ticker": "SPY", "action": "BUY", "shares": 100, "order_ref": "quant:inverse_vol"},
            {"ticker": "SPY", "action": "SELL", "shares": 40, "order_ref": "quant:inverse_vol"},
            {"ticker": "TLT", "action": "SELL_TO_OPEN", "shares": 10, "order_ref": "quant:inverse_vol"},
            {
                "ticker": "GLD",
                "action": "BUY_OPTION",
                "contracts": 3,
                "strike": 200.0,
                "order_ref": "quant:inverse_vol",
            },
            {"ticker": "IEF", "action": "BUY", "shares": 5},
            {"ticker": "IEF", "action": "SELL", "shares": 5},
        ]
        assert qe.sleeve_holdings(fills) == {"SPY": 60.0, "TLT": -10.0}


class TestAdmission:
    def test_requires_targets_rebalance_freshness_and_no_prior_orders(self):
        rows = [_row("SPY", 1.0, 1000.0, 100.0)]
        assert qe.admit(None, today=TODAY, force=False, prior_orders=[]) == (False, "no_targets")
        assert qe.admit(_targets(rows, rebalance=False), today=TODAY, force=False, prior_orders=[]) == (
            False,
            "not_rebalance_session",
        )
        assert qe.admit(_targets(rows, as_of="2026-10-30"), today=TODAY, force=False, prior_orders=[]) == (
            False,
            "stale_targets:8d",
        )
        assert qe.admit(_targets(rows), today=TODAY, force=False, prior_orders=[{"symbol": "SPY"}]) == (
            False,
            "already_executed",
        )
        assert qe.admit(_targets(rows), today=TODAY, force=False, prior_orders=[]) == (True, "ok")

    def test_force_overrides_rebalance_and_prior_but_not_staleness(self):
        rows = [_row("SPY", 1.0, 1000.0, 100.0)]
        assert qe.admit(_targets(rows, rebalance=False), today=TODAY, force=True, prior_orders=[{"x": 1}]) == (
            True,
            "ok",
        )
        assert qe.admit(_targets(rows, as_of="2026-10-01"), today=TODAY, force=True, prior_orders=[])[0] is False


class TestWire:
    ROWS = [_row("SPY", 0.6, 60_000.0, 500.0), _row("TLT", 0.4, 40_000.0, 100.0)]

    def test_dry_run_never_calls_the_placer_and_logs_planned_rows(self):
        deps, calls = _deps(targets=_targets(self.ROWS), fills=[])
        result = qe.run_strategy("inverse_vol", deps=deps, execute=False, today=TODAY, env={})
        assert result["admitted"] and result["mode"] == "dry-run"
        assert calls["placed"] == []
        assert [(o["symbol"], o["action"], o["quantity"], o["status"]) for o in result["orders"]] == [
            ("SPY", "BUY", 120, "planned"),
            ("TLT", "BUY", 400, "planned"),
        ]

    def test_execute_places_the_exact_stock_order_payload(self):
        fills = [{"ticker": "SPY", "action": "BUY", "shares": 20, "order_ref": "quant:inverse_vol"}]
        deps, calls = _deps(targets=_targets(self.ROWS), fills=fills)
        result = qe.run_strategy("inverse_vol", deps=deps, execute=True, today=TODAY, env={"RADON_QUANT_EXECUTE": "1"})
        assert calls["placed"] == [
            {
                "type": "stock",
                "symbol": "SPY",
                "action": "BUY",
                "quantity": 100,
                "limitPrice": 501.0,
                "tif": "DAY",
                "orderRef": "quant:inverse_vol",
            },
            {
                "type": "stock",
                "symbol": "TLT",
                "action": "BUY",
                "quantity": 400,
                "limitPrice": 100.2,
                "tif": "DAY",
                "orderRef": "quant:inverse_vol",
            },
        ]
        assert all(o["status"] == "ok" and o["order_id"] == 7 and o["perm_id"] == 99 for o in result["orders"])

    def test_rejections_and_indeterminate_outcomes_are_recorded_not_raised(self):
        def placer(params):
            if params["symbol"] == "SPY":
                return {"status": "error", "message": "201 shares not available"}
            return {"status": "error", "indeterminate": True, "orderRef": "quant:inverse_vol"}

        deps, calls = _deps(targets=_targets(self.ROWS), placer=placer)
        result = qe.run_strategy("inverse_vol", deps=deps, execute=True, today=TODAY, env={})
        statuses = {o["symbol"]: (o["status"], o["message"]) for o in result["orders"]}
        assert statuses["SPY"] == ("error", "201 shares not available")
        assert statuses["TLT"][0] == "indeterminate"

    def test_not_admitted_places_nothing(self):
        deps, calls = _deps(targets=_targets(self.ROWS, rebalance=False))
        result = qe.run_strategy("inverse_vol", deps=deps, execute=True, today=TODAY, env={})
        assert result["admitted"] is False and result["orders"] == [] and calls["placed"] == []

    def test_short_targets_need_long_short_strategy_and_env_flag(self):
        rows = [_row("USO", -0.5, -50_000.0, 70.0)]
        deps, calls = _deps(targets=_targets(rows))
        plain = qe.run_strategy("trend_inverse_vol", deps=deps, execute=False, today=TODAY, env={})
        assert plain["allow_short"] is False and plain["orders"] == []
        armed = qe.run_strategy(
            "trend_inverse_vol", deps=deps, execute=False, today=TODAY, env={"RADON_QUANT_ALLOW_SHORT": "1"}
        )
        assert armed["allow_short"] is True and armed["orders"][0]["action"] == "SELL"
        long_only = qe.run_strategy(
            "inverse_vol", deps=deps, execute=False, today=TODAY, env={"RADON_QUANT_ALLOW_SHORT": "1"}
        )
        assert long_only["allow_short"] is False


class TestDurability:
    """R-748 / REL-337: a timer retry must not double-trade.

    The 09:35 oneshot persists after ``place_order``. Turso down or a
    SIGKILL after the broker ack leaves ``quant_orders`` empty, so the
    Persistent=true retry admits the same session and places again.
    """

    ROWS = [_row("SPY", 1.0, 10_000.0, 100.0)]

    def test_persist_failure_does_not_reach_the_broker(self):
        def persist(key, result):
            raise RuntimeError("turso down")

        deps, calls = _deps(targets=_targets(self.ROWS), persist=persist)
        with pytest.raises(RuntimeError, match="turso down"):
            qe.run_strategy(
                "inverse_vol",
                deps=deps,
                execute=True,
                persist=True,
                today=TODAY,
                env={"RADON_QUANT_EXECUTE": "1"},
            )
        assert calls["placed"] == []

    def test_scheduled_persist_failure_does_not_place_and_retry_is_still_blocked(self):
        def persist(key, result):
            raise RuntimeError("turso down")

        deps, calls = _deps(targets=_targets(self.ROWS), persist=persist)
        env = {"RADON_QUANT_STRATEGIES": "inverse_vol", "RADON_QUANT_EXECUTE": "1"}
        assert qe.main(["--scheduled"], deps=deps, env=env) == 1
        assert calls["placed"] == []
        service, state, kw = calls["health"][0]
        assert (service, state) == ("quant-execute", "error") and "turso down" in kw["error"]["message"]

        deps2, calls2 = _deps(targets=_targets(self.ROWS), persist=persist)
        assert qe.main(["--scheduled"], deps=deps2, env=env) == 1
        assert calls2["placed"] == []

    def test_claim_is_persisted_before_the_first_broker_call(self):
        order: list = []

        def persist(key, result):
            order.append(("persist", [o["status"] for o in result["orders"]]))

        def placer(params):
            order.append(("place", params["symbol"]))
            return {"status": "ok", "orderId": 1, "permId": 2}

        deps, _calls = _deps(targets=_targets(self.ROWS), persist=persist, placer=placer)
        result = qe.run_strategy(
            "inverse_vol",
            deps=deps,
            execute=True,
            persist=True,
            today=TODAY,
            env={"RADON_QUANT_EXECUTE": "1"},
        )
        assert order[0] == ("persist", ["planned"])
        assert order[1] == ("place", "SPY")
        assert order[2] == ("persist", ["ok"])
        assert result["orders"][0]["status"] == "ok"


class TestCli:
    def test_scheduled_runs_enabled_strategies_and_heartbeats(self, capsys):
        deps, calls = _deps(targets=_targets([_row("SPY", 1.0, 10_000.0, 100.0)]))
        rc = qe.main(
            ["--scheduled"],
            deps=deps,
            env={"RADON_QUANT_STRATEGIES": "inverse_vol,dual_momentum", "RADON_QUANT_EXECUTE": "1"},
        )
        assert rc == 0
        assert [p["symbol"] for p in calls["placed"]] == ["SPY", "SPY"]
        persisted = [(k, r["mode"], [o["status"] for o in r["orders"]]) for k, r in calls["persisted"]]
        assert persisted == [
            ("inverse_vol", "execute", ["planned"]),
            ("inverse_vol", "execute", ["ok"]),
            ("dual_momentum", "execute", ["planned"]),
            ("dual_momentum", "execute", ["ok"]),
        ]
        assert [h[:2] for h in calls["health"]] == [("quant-execute", "ok")]

    def test_scheduled_with_nothing_enabled_is_a_clean_heartbeat(self):
        deps, calls = _deps(targets=None)
        assert qe.main(["--scheduled"], deps=deps, env={}) == 0
        assert calls["placed"] == [] and [h[:2] for h in calls["health"]] == [("quant-execute", "ok")]

    def test_scheduled_rejection_heartbeats_error(self):
        deps, calls = _deps(
            targets=_targets([_row("SPY", 1.0, 10_000.0, 100.0)]),
            placer=lambda params: {"status": "error", "message": "rejected"},
        )
        rc = qe.main(
            ["--scheduled"], deps=deps, env={"RADON_QUANT_STRATEGIES": "inverse_vol", "RADON_QUANT_EXECUTE": "1"}
        )
        assert rc == 1
        service, state, kw = calls["health"][0]
        assert (service, state) == ("quant-execute", "error") and "rejected" in kw["error"]["message"]

    def test_manual_json_and_exit_codes(self, capsys):
        deps, calls = _deps(targets=_targets([_row("SPY", 1.0, 10_000.0, 100.0)]))
        assert qe.main(["--strategy", "nope"], deps=deps, env={}) == 2
        assert qe.main([], deps=deps, env={}) == 2
        rc = qe.main(["--strategy", "inverse_vol", "--json", "--persist"], deps=deps, env={})
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["mode"] == "dry-run" and out["orders"][0]["status"] == "planned"
        assert calls["persisted"][0][0] == "inverse_vol" and calls["placed"] == []


class TestStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript((MIGRATIONS / "0109_quant_strategies.sql").read_text())
        db.executescript((MIGRATIONS / "0110_quant_execution.sql").read_text())
        return db

    def test_migration_110_adds_columns_table_and_version(self):
        db = self._db()
        versions = {r[0] for r in db.execute("SELECT version FROM schema_migrations")}
        assert versions == {109, 110}
        targets = {r[1] for r in db.execute("PRAGMA table_info(quant_targets)")}
        assert {"close", "rebalance_today"} <= targets
        orders = {r[1] for r in db.execute("PRAGMA table_info(quant_orders)")}
        assert {
            "strategy",
            "as_of",
            "symbol",
            "action",
            "quantity",
            "limit_price",
            "order_ref",
            "status",
            "order_id",
            "perm_id",
            "message",
            "placed_at",
        } <= orders

    def test_order_insert_sql_is_idempotent_and_checked(self):
        from db import writer

        db = self._db()
        args = (
            "inverse_vol",
            "2026-11-06",
            "SPY",
            "BUY",
            100,
            501.0,
            "quant:inverse_vol",
            "planned",
            None,
            None,
            None,
            "2026-11-07T14:35:00Z",
        )
        db.execute(writer.QUANT_ORDER_INSERT_SQL, args)
        db.execute(writer.QUANT_ORDER_INSERT_SQL, args[:7] + ("ok", 7, 99, None, "2026-11-07T14:35:00Z"))
        assert db.execute("SELECT status, order_id, perm_id FROM quant_orders").fetchall() == [("ok", 7, 99)]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(writer.QUANT_ORDER_INSERT_SQL, args[:3] + ("HOLD",) + args[4:])

    def test_readers_round_trip_latest_targets_and_orders(self):
        from db import readers, writer

        db = self._db()
        db.execute(writer.QUANT_TARGET_UPSERT_SQL, ("inverse_vol", "2026-11-05", "SPY", 0.5, 50_000.0, 490.0, 0, "t0"))
        db.execute(writer.QUANT_TARGET_UPSERT_SQL, ("inverse_vol", "2026-11-06", "SPY", 0.6, 60_000.0, 500.0, 1, "t1"))
        db.execute(writer.QUANT_TARGET_UPSERT_SQL, ("inverse_vol", "2026-11-06", "TLT", 0.4, 40_000.0, 100.0, 1, "t1"))
        db.execute(writer.QUANT_TARGET_UPSERT_SQL, ("dual_momentum", "2026-11-06", "GLD", 1.0, 1.0, 1.0, 1, "t1"))
        latest = readers.read_latest_quant_targets("inverse_vol", db=db)
        assert latest["as_of"] == "2026-11-06" and latest["rebalance_today"] is True
        assert [(r["symbol"], r["weight"], r["close"]) for r in latest["rows"]] == [
            ("SPY", 0.6, 500.0),
            ("TLT", 0.4, 100.0),
        ]
        assert readers.read_latest_quant_targets("nope", db=db) is None
        db.execute(
            writer.QUANT_ORDER_INSERT_SQL,
            ("inverse_vol", "2026-11-06", "SPY", "BUY", 100, 501.0, "quant:inverse_vol", "ok", 7, 99, None, "p1"),
        )
        assert readers.read_quant_orders("inverse_vol", "2026-11-06", db=db)[0]["symbol"] == "SPY"
        assert readers.read_quant_orders("inverse_vol", "2026-11-05", db=db) == []

    def test_sleeve_fills_reader_filters_on_order_ref(self):
        from db import readers

        db = self._db()
        db.execute(
            "CREATE TABLE journal (trade_id TEXT PRIMARY KEY, payload TEXT NOT NULL, filled_at TEXT, written_at TEXT NOT NULL)"
        )
        rows = [
            ("a", {"ticker": "SPY", "action": "BUY", "shares": 10, "order_ref": "quant:inverse_vol"}),
            ("b", {"ticker": "SPY", "action": "BUY", "shares": 10, "order_ref": "quant:inverse_vol_x"}),
            ("c", {"ticker": "SPY", "action": "BUY", "shares": 10, "order_ref": "radon-abc"}),
            ("d", {"ticker": "GLD", "action": "BUY", "shares": 10}),
        ]
        for tid, payload in rows:
            db.execute(
                "INSERT INTO journal VALUES (?, ?, ?, ?)", (tid, json.dumps(payload), "2026-11-07T14:40:00Z", "w")
            )
        fills = readers.read_quant_sleeve_fills("inverse_vol", db=db)
        assert [f["order_ref"] for f in fills] == ["quant:inverse_vol", "quant:inverse_vol_x"]
