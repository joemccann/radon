"""Level-triggered, pool-independent recovery for the stuck-pool-after-2FA deadlock.

ROOT CAUSE: after a 2FA approval the Gateway authenticates, but the FastAPI
ib_pool can stay stuck (all roles connected=False / account-less). The existing
recovery (`handle_auth_state_transition`) is EDGE-triggered on
`awaiting_2fa -> authenticated`, and `auth_state` is derived FROM the pool — so
while the pool is wedged every probe derives "awaiting_2fa", the edge never
fires, and the external approval is invisible forever. The only manual fix was
`systemctl restart radon-api.service`.

`recover_stuck_pool` breaks the loop: it is LEVEL-triggered and pool-INDEPENDENT
(probes the Gateway directly with throwaway clientId 98), never touches the 2FA
push lock, and never restarts the Gateway. These tests pin that contract.

See feedback_ib_pool_stuck_after_2fa.md.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.api import ib_gateway


@pytest.fixture(autouse=True)
def _no_real_2fa_lock(tmp_path, monkeypatch):
    """Redirect the cross-process 2FA push lock to a tmp file so a stray
    acquire (there should be NONE from this path) can never touch the
    production /var/lib/radon path.
    """
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))
    yield


def _make_pool(connected_roles: dict[str, bool], accounts: list[str] | None = None):
    """MagicMock that quacks like IBPool for recover_stuck_pool.

    `connected_roles`: role -> is_connected bool. A connected role reports the
    given `accounts`; a disconnected one reports []. reconnect_all is an
    AsyncMock that does NOT mutate status (override per-test for the success
    path).
    """
    accounts = accounts if accounts is not None else ["U1234567"]
    pool = MagicMock()

    def status() -> dict:
        return {
            role: {
                "connected": connected_roles.get(role, False),
                "client_id": idx + 3,
                "managed_accounts": accounts if connected_roles.get(role, False) else [],
            }
            for idx, role in enumerate(("sync", "orders", "data"))
        }

    pool.status.side_effect = status
    pool.reconnect_all = AsyncMock(return_value={r: True for r in connected_roles})
    pool.reconnect_roles = AsyncMock(return_value={r: True for r in connected_roles})
    return pool


def _assert_no_push_no_restart(monkeypatch):
    """Patch restart_ib_gateway + the 2FA lock acquire as fail-loud sentinels.

    Returns nothing; the patched callables raise if invoked so any accidental
    push/restart from the recovery path fails the test loudly.
    """
    def _boom_restart(*a, **k):  # noqa: ANN001
        raise AssertionError("recover_stuck_pool must NEVER call restart_ib_gateway")

    def _boom_lock(*a, **k):  # noqa: ANN001
        raise AssertionError("recover_stuck_pool must NEVER acquire the 2FA push lock")

    monkeypatch.setattr(ib_gateway, "restart_ib_gateway", _boom_restart)
    monkeypatch.setattr(ib_gateway.ib_2fa_lock, "acquire_2fa_push_lock", _boom_lock)


# ---------------------------------------------------------------------------
# (1) stuck pool + probe authenticated → reconnect once, True, no push/restart
# ---------------------------------------------------------------------------


def test_stuck_pool_authenticated_probe_reconnects_once(monkeypatch):
    _assert_no_push_no_restart(monkeypatch)

    pool = _make_pool({"sync": False, "orders": False, "data": False})

    # The role-scoped reconnect flips the pool to connected+accounted so the
    # verified re-read returns True.
    async def _reconnect(roles):
        pool.status.side_effect = lambda: {
            "sync": {"connected": True, "client_id": 3, "managed_accounts": ["U1234567"]},
            "orders": {"connected": True, "client_id": 4, "managed_accounts": ["U1234567"]},
            "data": {"connected": True, "client_id": 5, "managed_accounts": ["U1234567"]},
        }
        return {role: True for role in roles}

    pool.reconnect_roles = AsyncMock(side_effect=_reconnect)

    async def _fake_probe(timeout=8.0):
        return (True, ["U1234567"])

    monkeypatch.setattr(ib_gateway, "_probe_authenticated", _fake_probe)

    recovered = asyncio.run(ib_gateway.recover_stuck_pool(pool))

    assert recovered is True
    pool.reconnect_roles.assert_awaited_once()


# ---------------------------------------------------------------------------
# (2) genuine 2FA wait (probe not authenticated) → no reconnect, no push/restart
# ---------------------------------------------------------------------------


def test_genuine_2fa_wait_does_not_reconnect_or_restart(monkeypatch):
    _assert_no_push_no_restart(monkeypatch)

    pool = _make_pool({"sync": False, "orders": False, "data": False})

    async def _fake_probe(timeout=8.0):
        return (False, [])

    monkeypatch.setattr(ib_gateway, "_probe_authenticated", _fake_probe)

    recovered = asyncio.run(ib_gateway.recover_stuck_pool(pool))

    assert recovered is False
    pool.reconnect_all.assert_not_awaited()
    pool.reconnect_roles.assert_not_awaited()


# ---------------------------------------------------------------------------
# (3) fully-connected pool → no-op, no probe, no reconnect
# ---------------------------------------------------------------------------


def test_healthy_pool_is_noop_without_probing(monkeypatch):
    _assert_no_push_no_restart(monkeypatch)

    pool = _make_pool({"sync": True, "orders": True, "data": True})

    probe_calls = {"n": 0}

    async def _fake_probe(timeout=8.0):
        probe_calls["n"] += 1
        return (True, ["U1234567"])

    monkeypatch.setattr(ib_gateway, "_probe_authenticated", _fake_probe)

    recovered = asyncio.run(ib_gateway.recover_stuck_pool(pool))

    assert recovered is False
    assert probe_calls["n"] == 0, "healthy pool must short-circuit before probing"
    pool.reconnect_all.assert_not_awaited()
    pool.reconnect_roles.assert_not_awaited()


# ---------------------------------------------------------------------------
# (3b) None pool → no-op
# ---------------------------------------------------------------------------


def test_none_pool_is_noop():
    assert asyncio.run(ib_gateway.recover_stuck_pool(None)) is False


# ---------------------------------------------------------------------------
# DEADLOCK-PROOF regression: exact live signature
#   port listening, all roles connected=False, Gateway probe authenticated.
# The OLD edge path would derive auth_state="awaiting_2fa" (pool has no
# connected+accounted slot), never fire the awaiting_2fa->authenticated edge,
# and issue 0 reconnects. The NEW path issues exactly 1.
# ---------------------------------------------------------------------------


def test_deadlock_signature_new_path_issues_exactly_one_reconnect(monkeypatch):
    _assert_no_push_no_restart(monkeypatch)

    pool = _make_pool({"sync": False, "orders": False, "data": False})

    # Sanity: the OLD edge handler derives awaiting_2fa from this exact pool
    # status, proving the deadlock — no transition edge can fire.
    derived = ib_gateway._derive_auth_state({"port_listening": True}, pool.status())
    assert derived == "awaiting_2fa"

    async def _reconnect(roles):
        pool.status.side_effect = lambda: {
            "sync": {"connected": True, "client_id": 3, "managed_accounts": ["U1234567"]},
            "orders": {"connected": True, "client_id": 4, "managed_accounts": ["U1234567"]},
            "data": {"connected": True, "client_id": 5, "managed_accounts": ["U1234567"]},
        }
        return {role: True for role in roles}

    pool.reconnect_roles = AsyncMock(side_effect=_reconnect)

    async def _fake_probe(timeout=8.0):
        return (True, ["U1234567"])

    monkeypatch.setattr(ib_gateway, "_probe_authenticated", _fake_probe)

    recovered = asyncio.run(ib_gateway.recover_stuck_pool(pool))

    assert recovered is True
    pool.reconnect_roles.assert_awaited_once()


# ---------------------------------------------------------------------------
# Verified-fail: probe authenticated but reconnect doesn't take → returns False
# (used by the escalation ladder in server.py).
# ---------------------------------------------------------------------------


def test_reconnect_does_not_take_returns_false(monkeypatch):
    _assert_no_push_no_restart(monkeypatch)

    # Pool stays disconnected even after reconnect_all (reconnect didn't take).
    pool = _make_pool({"sync": False, "orders": False, "data": False})

    async def _fake_probe(timeout=8.0):
        return (True, ["U1234567"])

    monkeypatch.setattr(ib_gateway, "_probe_authenticated", _fake_probe)

    recovered = asyncio.run(ib_gateway.recover_stuck_pool(pool))

    assert recovered is False
    pool.reconnect_roles.assert_awaited_once()


# ---------------------------------------------------------------------------
# Bounded: a wedged reconnect_all is capped by reconnect_timeout (won't hang).
# ---------------------------------------------------------------------------


def test_reconnect_bounded_by_timeout(monkeypatch):
    _assert_no_push_no_restart(monkeypatch)

    pool = _make_pool({"sync": False, "orders": False, "data": False})

    async def _slow_reconnect(*a, **k):  # noqa: ANN001
        await asyncio.sleep(10)
        return {}

    pool.reconnect_all = _slow_reconnect
    pool.reconnect_roles = _slow_reconnect

    async def _fake_probe(timeout=8.0):
        return (True, ["U1234567"])

    monkeypatch.setattr(ib_gateway, "_probe_authenticated", _fake_probe)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(ib_gateway.recover_stuck_pool(pool, reconnect_timeout=0.05))


# ---------------------------------------------------------------------------
# R-060: a wedged data role must be recovered ROLE-SCOPED — live orders/sync
# sessions are never torn down (reconnect churn risks Gateway 2FA cycles), and
# healthy money roles must not mask a still-stuck data role from the ladder.
# ---------------------------------------------------------------------------


def test_wedged_data_role_reconnects_only_data_never_money_roles(monkeypatch):
    _assert_no_push_no_restart(monkeypatch)

    pool = _make_pool({"sync": True, "orders": True, "data": False})

    async def _reconnect_roles(roles):
        assert list(roles) == ["data"]
        pool.status.side_effect = lambda: {
            "sync": {"connected": True, "client_id": 3, "managed_accounts": ["U1234567"]},
            "orders": {"connected": True, "client_id": 4, "managed_accounts": ["U1234567"]},
            "data": {"connected": True, "client_id": 5, "managed_accounts": ["U1234567"]},
        }
        return {"data": True}

    pool.reconnect_roles = AsyncMock(side_effect=_reconnect_roles)

    async def _fake_probe(timeout=8.0):
        return (True, ["U1234567"])

    monkeypatch.setattr(ib_gateway, "_probe_authenticated", _fake_probe)

    recovered = asyncio.run(ib_gateway.recover_stuck_pool(pool))

    assert recovered is True
    pool.reconnect_all.assert_not_awaited()
    pool.reconnect_roles.assert_awaited_once()


def test_healthy_money_roles_do_not_mask_still_stuck_data_role(monkeypatch):
    _assert_no_push_no_restart(monkeypatch)

    # data stays disconnected even after the reconnect attempt — the ladder
    # must see recovered=False despite connected+accounted orders/sync.
    pool = _make_pool({"sync": True, "orders": True, "data": False})
    pool.reconnect_roles = AsyncMock(return_value={"data": False})

    async def _fake_probe(timeout=8.0):
        return (True, ["U1234567"])

    monkeypatch.setattr(ib_gateway, "_probe_authenticated", _fake_probe)

    recovered = asyncio.run(ib_gateway.recover_stuck_pool(pool))

    assert recovered is False, (
        "healthy orders/sync must not report a wedged data role as recovered"
    )
    pool.reconnect_all.assert_not_awaited()
