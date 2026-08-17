#!/usr/bin/env python3
"""Operator preference store — registry, precedence, caching and writes.

The safety-critical property under test: a stored (DB) value can NEVER
widen a hard cap. A row outside the registry band is discarded, not
clamped, so a corrupt ``app_preferences`` row cannot raise
``RADON_MAX_ORDER_NOTIONAL`` past its declared ceiling. The order path
(``order_limits.check_order_limits``) must also stay pure in-process
computation: a dead Turso resolves to env/default without raising and
without blocking the caller.
"""

import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_preferences  # noqa: E402
import order_limits  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_preferences(monkeypatch):
    app_preferences.clear_snapshot_for_tests()
    app_preferences.enable_background_refresh(False)
    for pref in app_preferences.REGISTRY:
        monkeypatch.delenv(pref.key, raising=False)
    yield
    app_preferences.clear_snapshot_for_tests()
    app_preferences.enable_background_refresh(False)


class TestRegistryIntegrity:
    def test_registry_keys_are_unique_and_nonempty(self):
        keys = [pref.key for pref in app_preferences.registry()]
        assert keys, "registry must not be empty"
        assert all(key.strip() for key in keys)
        assert len(set(keys)) == len(keys)

    def test_every_entry_is_well_formed(self):
        for pref in app_preferences.registry():
            assert pref.value_type in {"int", "float", "bool"}
            assert pref.label.strip()
            assert pref.description.strip()
            assert pref.group in app_preferences.GROUP_ORDER
            assert isinstance(pref.unit, str)
            assert isinstance(pref.applies_immediately, bool)
            if pref.value_type in {"int", "float"}:
                assert pref.hard_min <= pref.default <= pref.hard_max

    def test_registry_contains_all_five_order_limit_keys(self):
        keys = {pref.key for pref in app_preferences.registry()}
        assert {
            "RADON_MAX_ORDER_QTY",
            "RADON_MAX_STOCK_ORDER_QTY",
            "RADON_MAX_ORDER_NOTIONAL",
            "RADON_MAX_ORDERS_PER_MIN",
            "RADON_WORKFLOW_MAX_ORDERS",
        } <= keys

    def test_no_em_dash_in_labels_or_descriptions(self):
        for pref in app_preferences.registry():
            assert "—" not in pref.label
            assert "—" not in pref.description

    def test_to_dict_shape_is_the_documented_contract(self):
        entry = app_preferences.resolve("RADON_MAX_ORDER_QTY").to_dict()
        assert list(entry.keys()) == [
            "key",
            "label",
            "group",
            "value_type",
            "value",
            "default",
            "hard_min",
            "hard_max",
            "unit",
            "description",
            "applies_immediately",
            "source",
            "db_rejected",
            "updated_at",
            "updated_by",
        ]
        assert entry["value"] == 500
        assert entry["applies_immediately"] is True
        assert entry["db_rejected"] is False
        assert entry["updated_at"] is None


class TestPrecedence:
    def test_default_when_no_env_and_no_db(self):
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 500
        assert resolved.source == "default"

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "250")
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 250
        assert resolved.source == "env"

    def test_env_above_hard_max_is_clamped(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "999999")
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 2500
        assert resolved.source == "env"

    def test_env_below_hard_min_is_clamped(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "0")
        assert app_preferences.resolve("RADON_MAX_ORDER_QTY").value == 1

    def test_unparseable_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "abc")
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 500
        assert resolved.source == "default"

    def test_db_row_beats_env(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "300")
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "250"})
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 250
        assert resolved.source == "db"
        assert resolved.updated_at == "2026-01-01T00:00:00Z"
        assert resolved.updated_by == "test"

    def test_db_row_out_of_band_is_ignored_not_clamped(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "300")
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "999999"})
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value != 2500, "a DB row must never be clamped up to the cap"
        assert resolved.value == 300
        assert resolved.source == "env"
        assert resolved.db_rejected is True

    def test_db_row_below_hard_min_is_ignored(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "300")
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "0"})
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 300
        assert resolved.source == "env"
        assert resolved.db_rejected is True

    def test_db_row_out_of_band_with_no_env_uses_default(self):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_NOTIONAL": "99000000.0"})
        resolved = app_preferences.resolve("RADON_MAX_ORDER_NOTIONAL")
        assert resolved.value == 250000.0
        assert resolved.source == "default"
        assert resolved.db_rejected is True

    def test_db_row_unparseable_is_ignored(self):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "abc"})
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 500
        assert resolved.source == "default"
        assert resolved.db_rejected is True

    def test_bool_parsing_accepts_common_forms(self):
        """No bool key ships today, so the parser is covered directly rather
        than through a registry entry nothing reads."""
        for raw in ("true", "1", "yes", "on", "TRUE", " On "):
            assert app_preferences._parse_text("bool", raw) is True
        for raw in ("false", "0", "no", "off", "OFF"):
            assert app_preferences._parse_text("bool", raw) is False
        assert app_preferences._parse_text("bool", "maybe") is None

    def test_int_preference_accepts_integral_float_text(self):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "250.0"})
        assert app_preferences.get_int("RADON_MAX_ORDER_QTY") == 250

    def test_float_preference_resolves_as_float(self):
        resolved = app_preferences.resolve("RADON_MAX_ORDER_NOTIONAL")
        assert resolved.value == 250000.0
        assert isinstance(app_preferences.get_float("RADON_MAX_ORDER_NOTIONAL"), float)

    def test_resolve_unknown_key_raises_key_error(self):
        with pytest.raises(KeyError):
            app_preferences.resolve("RADON_NOT_A_PREFERENCE")


class TestFailureIsolation:
    def test_resolve_never_raises_when_store_unavailable(self, monkeypatch):
        def _boom(sql, args=(), timeout=None):
            raise RuntimeError("turso is down")

        monkeypatch.setattr(app_preferences, "_hrana_query", _boom)
        assert app_preferences.refresh_snapshot() is False
        resolved = app_preferences.resolve_all()
        assert len(resolved) == len(app_preferences.REGISTRY)
        assert all(item.source in {"env", "default"} for item in resolved)
        status = app_preferences.store_status()
        assert status["available"] is False
        assert status["error"]

    def test_order_path_survives_a_dead_store(self, monkeypatch):
        def _boom(sql, args=(), timeout=None):
            raise RuntimeError("turso is down")

        monkeypatch.setattr(app_preferences, "_hrana_query", _boom)
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "100")
        app_preferences.refresh_snapshot()
        violation = order_limits.check_order_limits({
            "type": "option", "symbol": "GOOG", "action": "BUY",
            "quantity": 101, "limitPrice": 1.0,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_QTY_LIMIT"
        assert order_limits.check_order_limits({
            "type": "option", "symbol": "GOOG", "action": "BUY",
            "quantity": 44, "limitPrice": 1.0,
        }) is None

    def test_hot_path_does_no_inline_io(self, monkeypatch):
        idents = []

        def _slow_refresh(timeout=app_preferences.DB_READ_TIMEOUT_S):
            idents.append(threading.get_ident())
            time.sleep(3)
            return False

        monkeypatch.setattr(app_preferences, "refresh_snapshot", _slow_refresh)
        started = time.monotonic()
        app_preferences.get_int("RADON_MAX_ORDER_QTY")
        app_preferences.get_int("RADON_MAX_ORDER_QTY")
        elapsed = time.monotonic() - started
        assert elapsed < 0.1
        assert threading.get_ident() not in idents

    def test_cache_ttl_bounds_refresh_count(self, monkeypatch):
        monkeypatch.setattr(app_preferences, "_db_enabled", lambda: True)
        app_preferences.enable_background_refresh(True)
        calls = []

        def _counting_refresh(timeout=app_preferences.DB_READ_TIMEOUT_S):
            calls.append(1)
            return True

        monkeypatch.setattr(app_preferences, "refresh_snapshot", _counting_refresh)
        for _ in range(20):
            app_preferences.resolve("RADON_MAX_ORDER_QTY")
            time.sleep(0.005)
        time.sleep(0.1)
        assert len(calls) == 1

    def test_failed_refresh_keeps_last_known_good(self, monkeypatch):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "250"})

        def _boom(sql, args=(), timeout=None):
            raise RuntimeError("turso is down")

        monkeypatch.setattr(app_preferences, "_hrana_query", _boom)
        assert app_preferences.refresh_snapshot() is False
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 250
        assert resolved.source == "db"

    def test_refresh_snapshot_loads_rows(self, monkeypatch):
        monkeypatch.setattr(
            app_preferences,
            "_hrana_query",
            lambda sql, args=(), timeout=None: [
                ("RADON_MAX_ORDER_QTY", "250", "2026-08-11T00:00:00Z", "user_a"),
            ],
        )
        assert app_preferences.refresh_snapshot() is True
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 250
        assert resolved.source == "db"
        assert resolved.updated_by == "user_a"
        assert app_preferences.store_status()["available"] is True


class TestValidation:
    def test_coerce_value_unknown_key_raises_unknown_preference(self):
        with pytest.raises(app_preferences.PreferenceValidationError) as exc:
            app_preferences.coerce_value("RADON_NOPE", 1)
        assert exc.value.code == "UNKNOWN_PREFERENCE"
        assert "RADON_NOPE" in exc.value.message

    def test_coerce_value_wrong_type_raises_wrong_type(self):
        with pytest.raises(app_preferences.PreferenceValidationError) as exc:
            app_preferences.coerce_value("RADON_MAX_ORDER_QTY", 400.5)
        assert exc.value.code == "WRONG_TYPE"
        assert "integer" in exc.value.message
        with pytest.raises(app_preferences.PreferenceValidationError) as exc:
            app_preferences.coerce_value("RADON_MAX_ORDER_NOTIONAL", "banana")
        assert exc.value.code == "WRONG_TYPE"
        with pytest.raises(app_preferences.PreferenceValidationError) as exc:
            app_preferences.coerce_value("RADON_MAX_ORDER_QTY", True)
        assert exc.value.code == "WRONG_TYPE"

    def test_coerce_value_accepts_valid_forms(self):
        assert app_preferences.coerce_value("RADON_MAX_ORDER_QTY", "400") == 400
        assert app_preferences.coerce_value("RADON_MAX_ORDER_QTY", 400.0) == 400
        assert app_preferences.coerce_value("RADON_MAX_ORDER_NOTIONAL", 300000) == 300000.0

    def test_coerce_value_out_of_range_raises_with_allowed_bounds(self):
        with pytest.raises(app_preferences.PreferenceValidationError) as exc:
            app_preferences.coerce_value("RADON_MAX_ORDER_QTY", 9000)
        assert exc.value.code == "OUT_OF_RANGE"
        assert exc.value.allowed_min == 1
        assert exc.value.allowed_max == 2500
        assert "1" in exc.value.message and "2500" in exc.value.message
        assert "9000" in exc.value.message


class TestWrites:
    def test_set_value_writes_canonical_text(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            app_preferences,
            "_hrana_execute",
            lambda sql, args=(), timeout=None: recorded.append((sql, tuple(args), timeout)),
        )
        app_preferences.set_value("RADON_MAX_ORDER_NOTIONAL", 300000)
        assert recorded[-1][1][0] == "RADON_MAX_ORDER_NOTIONAL"
        assert recorded[-1][1][1] == "300000.0"
        assert recorded[-1][2] == app_preferences.DB_WRITE_TIMEOUT_S

        app_preferences.set_value("RADON_MAX_ORDERS_PER_MIN", 20, updated_by="user_2abc")
        assert recorded[-1][1][1] == "20"
        assert recorded[-1][1][3] == "user_2abc"

        app_preferences.set_value("RADON_MAX_ORDER_QTY", 400, updated_by="u" * 200)
        assert recorded[-1][1][1] == "400"
        assert recorded[-1][1][3] == "u" * 64

    def test_set_value_updates_snapshot_immediately(self, monkeypatch):
        monkeypatch.setattr(
            app_preferences, "_hrana_execute", lambda sql, args=(), timeout=None: None
        )
        resolved = app_preferences.set_value("RADON_MAX_ORDER_QTY", 400, updated_by="user_x")
        assert resolved.value == 400
        assert resolved.source == "db"
        assert resolved.updated_by == "user_x"
        assert app_preferences.get_int("RADON_MAX_ORDER_QTY") == 400

    def test_set_value_rejects_out_of_range_before_writing(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            app_preferences,
            "_hrana_execute",
            lambda sql, args=(), timeout=None: recorded.append(args),
        )
        with pytest.raises(app_preferences.PreferenceValidationError):
            app_preferences.set_value("RADON_MAX_ORDER_QTY", 9000)
        assert recorded == []

    def test_set_value_store_failure_raises_and_leaves_snapshot_unchanged(self, monkeypatch):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "250"})

        def _boom(sql, args=(), timeout=None):
            raise RuntimeError("write failed")

        monkeypatch.setattr(app_preferences, "_hrana_execute", _boom)
        with pytest.raises(app_preferences.PreferenceStoreError):
            app_preferences.set_value("RADON_MAX_ORDER_QTY", 400)
        assert app_preferences.get_int("RADON_MAX_ORDER_QTY") == 250

    def test_clear_value_deletes_row_and_falls_back_to_env_then_default(self, monkeypatch):
        statements = []
        monkeypatch.setattr(
            app_preferences,
            "_hrana_execute",
            lambda sql, args=(), timeout=None: statements.append((sql, tuple(args))),
        )
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "250"})
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "300")
        resolved = app_preferences.clear_value("RADON_MAX_ORDER_QTY", updated_by="user_x")
        assert "DELETE" in statements[-1][0].upper()
        assert statements[-1][1] == ("RADON_MAX_ORDER_QTY",)
        assert resolved.value == 300
        assert resolved.source == "env"

        monkeypatch.delenv("RADON_MAX_ORDER_QTY", raising=False)
        assert app_preferences.resolve("RADON_MAX_ORDER_QTY").source == "default"

    def test_clear_value_unknown_key_raises(self):
        with pytest.raises(app_preferences.PreferenceValidationError) as exc:
            app_preferences.clear_value("RADON_NOPE")
        assert exc.value.code == "UNKNOWN_PREFERENCE"

    def test_apply_env_overlay_only_touches_registry_keys(self, monkeypatch):
        monkeypatch.setattr(
            app_preferences, "_hrana_execute", lambda sql, args=(), timeout=None: None
        )
        monkeypatch.setenv("RADON_NOT_A_PREFERENCE", "untouched")
        app_preferences.set_value("RADON_SCANNER_WORKERS", 8)
        import os

        assert os.environ["RADON_SCANNER_WORKERS"] == "8"
        assert os.environ["RADON_NOT_A_PREFERENCE"] == "untouched"

        app_preferences.clear_value("RADON_SCANNER_WORKERS")
        assert os.environ.get("RADON_SCANNER_WORKERS") is None

    def test_apply_env_overlay_restores_the_prior_environment_value(self, monkeypatch):
        monkeypatch.setattr(
            app_preferences, "_hrana_execute", lambda sql, args=(), timeout=None: None
        )
        monkeypatch.setenv("RADON_SCANNER_WORKERS", "12")
        app_preferences.set_value("RADON_SCANNER_WORKERS", 8)
        import os

        assert os.environ["RADON_SCANNER_WORKERS"] == "8"
        app_preferences.clear_value("RADON_SCANNER_WORKERS")
        assert os.environ["RADON_SCANNER_WORKERS"] == "12"


class TestOrderLimitsDelegation:
    def test_max_order_qty_reads_the_store(self):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "250"})
        assert order_limits.max_order_qty() == 250

    def test_every_accessor_delegates_to_the_store(self):
        app_preferences.seed_snapshot_for_tests({
            "RADON_MAX_STOCK_ORDER_QTY": "2000",
            "RADON_MAX_ORDER_NOTIONAL": "50000.0",
            "RADON_MAX_ORDERS_PER_MIN": "4",
            "RADON_WORKFLOW_MAX_ORDERS": "2",
        })
        assert order_limits.max_stock_order_qty() == 2000
        assert order_limits.max_order_notional() == 50000.0
        assert order_limits.max_orders_per_min() == 4
        assert order_limits.workflow_max_orders() == 2

    def test_check_order_limits_uses_stored_cap(self):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "100"})
        violation = order_limits.check_order_limits({
            "type": "option", "symbol": "GOOG", "action": "BUY",
            "quantity": 101, "limitPrice": 1.0,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_QTY_LIMIT"
        assert "RADON_MAX_ORDER_QTY" in violation["message"]

    def test_stored_cap_cannot_widen_past_the_hard_max(self):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_NOTIONAL": "99000000.0"})
        violation = order_limits.check_order_limits({
            "type": "option", "symbol": "CRCL", "action": "BUY",
            "quantity": 250, "limitPrice": 40.0,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_check_quantity_limit_still_applies_the_contract_cap(self):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "100"})
        assert order_limits.check_quantity_limit(101)["code"] == "ORDER_QTY_LIMIT"
        assert order_limits.check_quantity_limit(50) is None


class TestRegistryIsHonest:
    """Every declared key must be reachable by the process that reads it.

    A control surface whose described effect is false is worse than no
    control at all, so the registry carries only keys that radon-api (or a
    subprocess inheriting its environment) reads at call time.
    """

    def test_every_entry_applies_immediately(self):
        inert = [p.key for p in app_preferences.registry() if not p.applies_immediately]
        assert inert == [], f"registry declares keys nothing live reads: {inert}"

    def test_order_limit_bands_stay_within_a_small_multiple_of_the_default(self):
        for pref in app_preferences.registry():
            if pref.group != "Order Limits":
                continue
            assert pref.hard_max <= pref.default * 6, (
                f"{pref.key} band lets one click widen the cap "
                f"{pref.hard_max / pref.default:.0f}x past the default"
            )


class TestRefreshSpawnFailureIsContained:
    def test_thread_spawn_failure_never_reaches_the_order_path(self, monkeypatch):
        monkeypatch.setattr(app_preferences, "_db_enabled", lambda: True)
        app_preferences.enable_background_refresh(True)

        def _explode(self, *a, **k):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading.Thread, "start", _explode)
        assert order_limits.max_order_qty() == 500
        assert order_limits.check_order_limits({
            "type": "option", "quantity": 1, "limitPrice": 1.0,
        }) is None

    def test_thread_spawn_failure_does_not_latch_the_refreshing_flag(self, monkeypatch):
        monkeypatch.setattr(app_preferences, "_db_enabled", lambda: True)
        app_preferences.enable_background_refresh(True)
        calls = []

        def _explode(self, *a, **k):
            calls.append(1)
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading.Thread, "start", _explode)
        app_preferences.resolve("RADON_MAX_ORDER_QTY")
        app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert len(calls) == 2, "refresh latched after a spawn failure"


class TestShortLivedProcessesDoNotTouchTheStore:
    """ib_place_order.py is a fresh process per order. It must resolve from
    the inherited environment overlay, never open a Turso socket it will
    abandon at exit (feedback_turso_per_ip_socket_exhaustion_bounded_pool)."""

    def test_background_refresh_is_off_unless_explicitly_enabled(self, monkeypatch):
        app_preferences.enable_background_refresh(False)
        spawned = []
        monkeypatch.setattr(
            threading.Thread, "start", lambda self, *a, **k: spawned.append(1)
        )
        app_preferences.resolve("RADON_MAX_ORDER_QTY")
        app_preferences.resolve("RADON_MAX_ORDER_NOTIONAL")
        assert spawned == [], "a short-lived process spawned a store refresh"

    def test_bootstrap_publishes_stored_values_into_the_environment(self, monkeypatch):
        monkeypatch.setattr(
            app_preferences,
            "_hrana_query",
            lambda *a, **k: [("RADON_MAX_ORDER_QTY", "50", "2026-08-11T00:00:00Z", "op")],
        )
        assert app_preferences.bootstrap() is True
        assert os.environ["RADON_MAX_ORDER_QTY"] == "50"
        assert order_limits.max_order_qty() == 50

    def test_bootstrap_survives_a_dead_store(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("turso down")

        monkeypatch.setattr(app_preferences, "_hrana_query", _boom)
        assert app_preferences.bootstrap() is False
        assert order_limits.max_order_qty() == 500


class TestAuditTrail:
    def test_set_value_records_an_append_only_event_before_mutating(self, monkeypatch):
        statements = []
        monkeypatch.setattr(
            app_preferences,
            "_hrana_execute",
            lambda sql, args=(), timeout=0: statements.append((sql, tuple(args))),
        )
        app_preferences.set_value("RADON_MAX_ORDER_NOTIONAL", 400000.0, updated_by="user_abc")
        assert len(statements) == 2
        assert "app_preference_events" in statements[0][0], "audit event must be written first"
        assert "INSERT INTO app_preferences" in statements[1][0]
        event_args = statements[0][1]
        assert "RADON_MAX_ORDER_NOTIONAL" in event_args
        assert "user_abc" in event_args
        assert "set" in event_args

    def test_clear_value_records_the_actor_and_the_superseded_value(self, monkeypatch):
        app_preferences.seed_snapshot_for_tests({"RADON_MAX_ORDER_QTY": "50"})
        statements = []
        monkeypatch.setattr(
            app_preferences,
            "_hrana_execute",
            lambda sql, args=(), timeout=0: statements.append((sql, tuple(args))),
        )
        app_preferences.clear_value("RADON_MAX_ORDER_QTY", updated_by="user_abc")
        assert "app_preference_events" in statements[0][0]
        assert "user_abc" in statements[0][1]
        assert "reset" in statements[0][1]
        assert "50" in statements[0][1], "the superseded value must be recorded"

    def test_a_failed_audit_write_refuses_the_mutation(self, monkeypatch):
        def _boom(sql, args=(), timeout=0):
            raise RuntimeError("audit table unreachable")

        monkeypatch.setattr(app_preferences, "_hrana_execute", _boom)
        with pytest.raises(app_preferences.PreferenceStoreError):
            app_preferences.set_value("RADON_MAX_ORDER_QTY", 50, updated_by="op")
        assert order_limits.max_order_qty() == 500


class TestConcurrentWriteAndRefresh:
    def test_an_in_flight_refresh_cannot_roll_back_a_newer_write(self, monkeypatch):
        """The refresh SELECT is issued before the save lands, so its result
        is stale by the time it returns and must not clobber the new value."""
        released = threading.Event()
        # The race this pins is "SELECT issued, THEN the write lands". Waiting a
        # fixed 50ms for the worker to get there loses that ordering on a slow
        # runner: the worker captures the POST-write generation, adopts the stale
        # 500 row, and the assertion fails for a reason that is not the contract.
        # refresh_snapshot() reads _WRITE_GENERATION on its first line and calls
        # _hrana_query next, so this event fires strictly after that capture.
        entered = threading.Event()
        monkeypatch.setattr(app_preferences, "_hrana_execute", lambda *a, **k: None)

        def _slow_query(*a, **k):
            entered.set()
            released.wait(2.0)
            return [("RADON_MAX_ORDER_QTY", "500", "2026-08-11T00:00:00Z", "old")]

        monkeypatch.setattr(app_preferences, "_hrana_query", _slow_query)
        worker = threading.Thread(target=app_preferences.refresh_snapshot, daemon=True)
        worker.start()
        assert entered.wait(5.0), "the refresh never issued its SELECT"
        app_preferences.set_value("RADON_MAX_ORDER_QTY", 50, updated_by="op")
        released.set()
        worker.join(3.0)
        assert order_limits.max_order_qty() == 50


class TestStoreStatus:
    def test_a_successful_write_clears_a_stale_read_error(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("turso read down")

        monkeypatch.setattr(app_preferences, "_hrana_query", _boom)
        app_preferences.refresh_snapshot()
        assert app_preferences.store_status()["available"] is False

        monkeypatch.setattr(app_preferences, "_hrana_execute", lambda *a, **k: None)
        app_preferences.set_value("RADON_MAX_ORDER_QTY", 50, updated_by="op")
        assert app_preferences.store_status()["available"] is True


class TestOverlayDoesNotMasqueradeAsEnv:
    def test_a_removed_row_falls_back_to_the_code_default(self, monkeypatch):
        monkeypatch.setattr(app_preferences, "_hrana_execute", lambda *a, **k: None)
        app_preferences.set_value("RADON_MAX_ORDER_QTY", 50, updated_by="op")
        assert os.environ["RADON_MAX_ORDER_QTY"] == "50"

        app_preferences.clear_value("RADON_MAX_ORDER_QTY", updated_by="op")
        resolved = app_preferences.resolve("RADON_MAX_ORDER_QTY")
        assert resolved.value == 500
        assert resolved.source == "default"
