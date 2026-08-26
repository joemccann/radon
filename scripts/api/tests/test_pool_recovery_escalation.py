"""Escalation ladder + single-flight + cooldown for the server-side pool-recovery
guard (`_recover_stuck_pool_guarded` in scripts/api/server.py).

The guard wraps `recover_stuck_pool` on the 15s heartbeat. Contract pinned here:

  * single-flight — an in-progress recovery skips overlapping ticks.
  * cooldown — ticks inside POOL_RECOVERY_COOLDOWN_SECS are no-ops.
  * escalation — only a VERIFIED failure (Gateway probe authenticated yet pool
    still stuck) counts toward the ladder; after
    POOL_RECOVERY_MAX_BEFORE_RESTART consecutive verified failures it triggers
    exactly ONE radon-api self-restart, then resets the counter so it can never
    loop-restart. Success resets the counter to 0.

See feedback_ib_pool_stuck_after_2fa.md.
"""

from __future__ import annotations

import asyncio

import pytest

import scripts.api.server as server


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset the guard state, force out of test_mode (the guard short-circuits
    on test_mode by design), and install a sentinel pool + restart hook.
    """
    server._pool_recovery_state["in_progress"] = False
    server._pool_recovery_state["last_attempt_at"] = 0.0
    server._pool_recovery_state["consecutive_failures"] = 0
    monkeypatch.setattr(server, "test_mode", False)
    # A non-None pool so the guard does not short-circuit on `ib_pool is None`.
    monkeypatch.setattr(server, "ib_pool", object())
    yield
    server._pool_recovery_state["in_progress"] = False
    server._pool_recovery_state["last_attempt_at"] = 0.0
    server._pool_recovery_state["consecutive_failures"] = 0


def _patch_recover(monkeypatch, recovered_seq):
    """Patch server.recover_stuck_pool to yield successive bools from
    `recovered_seq`. Returns a call-counter dict.
    """
    calls = {"n": 0}
    seq = list(recovered_seq)

    async def _fake(pool, *a, **k):  # noqa: ANN001
        idx = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[idx]

    monkeypatch.setattr(server, "recover_stuck_pool", _fake)
    return calls


def _patch_verified_fail(monkeypatch, *, authenticated=True, disconnected=True, accounted=False):
    """Patch the ib_gateway helpers the guard re-imports for the escalation
    decision so a `recovered=False` result is classified as a VERIFIED failure
    (authenticated probe + disconnected slot + no accounted slot).
    """
    # The guard does `from api.ib_gateway import ...` at call time, which
    # resolves against the `api.ib_gateway` module object — NOT the
    # `scripts.api.ib_gateway` one (they are distinct module objects under this
    # repo's dual import roots). Patch the module the guard actually binds.
    import api.ib_gateway as ibg

    monkeypatch.setattr(
        ibg, "_pool_disconnected_roles", lambda pool: ["data"] if disconnected else []
    )
    monkeypatch.setattr(
        ibg, "_pool_has_connected_accounted_slot", lambda pool, roles=None: accounted
    )

    async def _probe(timeout=8.0):
        return (authenticated, ["U1"] if authenticated else [])

    monkeypatch.setattr(ibg, "_probe_authenticated", _probe)


def _patch_restart_hook(monkeypatch):
    restarts = {"n": 0}
    monkeypatch.setattr(server, "_restart_radon_api_self", lambda: restarts.__setitem__("n", restarts["n"] + 1))
    return restarts


# ---------------------------------------------------------------------------
# Escalation ladder: N verified-fails → exactly one restart at threshold, reset.
# ---------------------------------------------------------------------------


def test_escalation_only_at_threshold_then_resets(monkeypatch):
    _patch_recover(monkeypatch, [False])
    _patch_verified_fail(monkeypatch)
    restarts = _patch_restart_hook(monkeypatch)

    threshold = server.POOL_RECOVERY_MAX_BEFORE_RESTART

    # Drive `threshold` ticks, bypassing the cooldown each time by resetting
    # last_attempt_at to 0 (simulates ticks spaced beyond the cooldown).
    for i in range(threshold):
        server._pool_recovery_state["last_attempt_at"] = 0.0
        asyncio.run(server._recover_stuck_pool_guarded())
        if i < threshold - 1:
            assert restarts["n"] == 0, f"restart fired too early at tick {i}"

    assert restarts["n"] == 1, "exactly one restart at threshold"
    # Counter reset after escalation so it cannot loop-restart.
    assert server._pool_recovery_state["consecutive_failures"] == 0


def test_success_resets_failure_counter(monkeypatch):
    # Two verified-fails then a success: counter must drop back to 0, no restart.
    _patch_recover(monkeypatch, [False, False, True])
    _patch_verified_fail(monkeypatch)
    restarts = _patch_restart_hook(monkeypatch)

    for _ in range(3):
        server._pool_recovery_state["last_attempt_at"] = 0.0
        asyncio.run(server._recover_stuck_pool_guarded())

    assert restarts["n"] == 0
    assert server._pool_recovery_state["consecutive_failures"] == 0


def test_genuine_2fa_wait_never_escalates(monkeypatch):
    # recover returns False AND probe says not authenticated → genuine 2FA wait,
    # must NOT count toward the ladder no matter how many cycles.
    _patch_recover(monkeypatch, [False])
    _patch_verified_fail(monkeypatch, authenticated=False)
    restarts = _patch_restart_hook(monkeypatch)

    for _ in range(server.POOL_RECOVERY_MAX_BEFORE_RESTART + 2):
        server._pool_recovery_state["last_attempt_at"] = 0.0
        asyncio.run(server._recover_stuck_pool_guarded())

    assert restarts["n"] == 0
    assert server._pool_recovery_state["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Single-flight + cooldown.
# ---------------------------------------------------------------------------


def test_cooldown_skips_second_tick_in_window(monkeypatch):
    calls = _patch_recover(monkeypatch, [True])
    _patch_restart_hook(monkeypatch)

    # First tick runs (last_attempt_at starts at 0, now - 0 >> cooldown).
    asyncio.run(server._recover_stuck_pool_guarded())
    assert calls["n"] == 1
    # Second tick immediately after — inside the cooldown window → no-op.
    asyncio.run(server._recover_stuck_pool_guarded())
    assert calls["n"] == 1, "second tick inside cooldown must be a no-op"


def test_single_flight_skips_when_in_progress(monkeypatch):
    calls = _patch_recover(monkeypatch, [True])
    _patch_restart_hook(monkeypatch)

    server._pool_recovery_state["in_progress"] = True
    asyncio.run(server._recover_stuck_pool_guarded())
    assert calls["n"] == 0, "must skip while a recovery is already in progress"


def test_test_mode_short_circuits(monkeypatch):
    calls = _patch_recover(monkeypatch, [True])
    monkeypatch.setattr(server, "test_mode", True)
    asyncio.run(server._recover_stuck_pool_guarded())
    assert calls["n"] == 0, "guard must never touch IB under RADON_API_TEST_MODE"


# ---------------------------------------------------------------------------
# R-060: healthy money roles must not mask a wedged data role from the ladder.
# The predicate is NOT stubbed here: a status()-driven fake pool exercises the
# real role-scoped `_pool_has_connected_accounted_slot(ib_pool, roles=stuck)`
# call, so reverting to the ANY-role form goes red (T-105).
# ---------------------------------------------------------------------------

_WEDGED_DATA = {
    "sync": {"connected": True, "client_id": 3, "managed_accounts": ["U1"]},
    "orders": {"connected": True, "client_id": 4, "managed_accounts": ["U1"]},
    "data": {"connected": False, "client_id": 5, "managed_accounts": []},
}
_ALL_HEALTHY = {
    "sync": {"connected": True, "client_id": 3, "managed_accounts": ["U1"]},
    "orders": {"connected": True, "client_id": 4, "managed_accounts": ["U1"]},
    "data": {"connected": True, "client_id": 5, "managed_accounts": ["U1"]},
}


def _status_driven_pool(monkeypatch, snapshots):
    """Install a fake ib_pool whose status() yields successive snapshots
    (the last one sticks) and an authenticated Gateway probe. Only the probe
    is stubbed — the role-scoped accounted predicate runs for real.
    """
    from unittest.mock import MagicMock

    import api.ib_gateway as ibg

    queue = list(snapshots)
    pool = MagicMock()
    pool.status.side_effect = lambda: queue.pop(0) if len(queue) > 1 else queue[0]
    monkeypatch.setattr(server, "ib_pool", pool)

    async def _probe(timeout=8.0):
        return (True, ["U1"])

    monkeypatch.setattr(ibg, "_probe_authenticated", _probe)
    return pool


def _tick():
    server._pool_recovery_state["last_attempt_at"] = 0.0
    asyncio.run(server._recover_stuck_pool_guarded())


def test_wedged_data_with_healthy_money_roles_counts_toward_ladder(monkeypatch):
    """Exact wedge signature: orders/sync connected+accounted, data stuck,
    Gateway probe authenticated, recovery not taking. The ANY-role accounted
    check used to reset the counter every tick so os._exit never fired.
    """
    _patch_recover(monkeypatch, [False])
    restarts = _patch_restart_hook(monkeypatch)
    _status_driven_pool(monkeypatch, [_WEDGED_DATA])

    threshold = server.POOL_RECOVERY_MAX_BEFORE_RESTART
    for i in range(threshold - 1):
        _tick()
        assert server._pool_recovery_state["consecutive_failures"] == i + 1, (
            f"tick {i}: healthy orders/sync must not reset the ladder while data is wedged"
        )
        assert restarts["n"] == 0, f"restart fired too early at tick {i}"
    _tick()

    assert restarts["n"] == 1, (
        "wedged data role never counted toward the escalation ladder — the "
        "ANY-role accounted check reset it on healthy orders/sync"
    )
    assert server._pool_recovery_state["consecutive_failures"] == 0


def test_stuck_role_reconnecting_resets_counter_without_restart(monkeypatch):
    """Mirror case: the STUCK role itself comes back between the disconnected
    re-derivation and the accounted re-read (raced recovery). That re-read is
    the only thing allowed to clear the ladder; no restart may fire.
    """
    _patch_recover(monkeypatch, [False])
    restarts = _patch_restart_hook(monkeypatch)
    # Per tick the guard reads status() twice: disconnected-roles, then the
    # role-scoped accounted check. Two verified-fail ticks (4 wedged reads),
    # then a tick whose second read shows data recovered.
    _status_driven_pool(
        monkeypatch,
        [_WEDGED_DATA, _WEDGED_DATA, _WEDGED_DATA, _WEDGED_DATA, _WEDGED_DATA, _ALL_HEALTHY],
    )

    _tick()
    _tick()
    assert server._pool_recovery_state["consecutive_failures"] == 2

    _tick()
    assert server._pool_recovery_state["consecutive_failures"] == 0, (
        "role-scoped re-read showing data recovered must clear the ladder"
    )
    assert restarts["n"] == 0, "recovered data role must not escalate"

    for _ in range(server.POOL_RECOVERY_MAX_BEFORE_RESTART):
        _tick()
    assert restarts["n"] == 0
    assert server._pool_recovery_state["consecutive_failures"] == 0
