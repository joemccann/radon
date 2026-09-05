"""Shared pytest fixtures for the FastAPI tests.

This package is a separate collection subtree from scripts/tests, so the
orphan-state isolation contract has to be declared here too.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts/ to sys.path so tests can import `utils.*` the way the app does.
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from utils import ib_2fa_lock  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_ib_2fa_lock_orphan_state(monkeypatch):
    """Reset the process-wide orphan-confirmation memory around every test.

    Same contract as scripts/tests/conftest.py: R-210's `_orphan_reported` /
    `_orphan_seen_up` globals and the real inter-probe `time.sleep` are
    process-wide, and the api restart suites never reset them. T-226.
    """
    ib_2fa_lock.reset_orphan_state()
    monkeypatch.setattr(ib_2fa_lock, "ORPHAN_CONFIRM_INTERVAL_SECS", 0.0)
    yield
    ib_2fa_lock.reset_orphan_state()


@pytest.fixture(autouse=True)
def _isolate_jwks_negative_cache():
    """Reset auth's process-wide JWKS state around every test (REL-235).

    `_jwks_negative` and `_jwks_inflight` are module globals with a 30 s TTL;
    a kid cached in one test leaked a 401-from-cache into later tests (REL-210
    lesson). The module can be imported as `api.auth` or `scripts.api.auth`
    depending on the suite, so both module objects are cleared.
    """
    def _clear():
        for name in ("api.auth", "scripts.api.auth"):
            mod = sys.modules.get(name)
            if mod is not None:
                mod._jwks_negative.clear()
                mod._jwks_inflight.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _isolate_order_rate_budget():
    """Hand every test a fresh per-minute order budget.

    `server._order_rate_timestamps` is process-wide and holds every accepted
    placement for a rolling 60 s, capped at RADON_MAX_ORDERS_PER_MIN (10). A
    placement posted without an `orderRef` appends `None`, which the dedupe
    branch never matches, so cases spent a SHARED budget and the eleventh
    placement in a worker got a 429 it never asked for. Under
    `pytest -n auto --dist loadfile` the victim depends on file-to-worker
    assignment; on 2026-08-29 it was the /orders/place timeout contract, the
    last red job on main. Contract: test_order_rate_limit_isolation.py.
    """
    from scripts.api import server

    server._order_rate_timestamps.clear()
    yield
    server._order_rate_timestamps.clear()


def _credential_route_modules():
    from scripts.api.routes import credentials as credentials_module

    modules = [credentials_module]
    doppelganger = sys.modules.get("api.routes.credentials")
    if doppelganger is not None and doppelganger is not credentials_module:
        modules.append(doppelganger)
    return modules


@pytest.fixture(autouse=True)
def _isolate_credential_route_state():
    """Hand every test a clean credential-route process state.

    `routes.credentials._validator_last_run` is process-wide: an accepted
    PUT /credentials/{service_id} stamps the service and a second PUT inside
    VALIDATOR_COOLDOWN_S is a 429 VALIDATION_COOLDOWN. Three files PUT
    `unusual_whales` and only one reset the stamp, so under
    `pytest -n auto --dist loadfile` the worker assignment decided which
    file got the 429 (2026-09-03). The same PUT adds the name to the
    module-global `_SESSION_EXPORTED`, which flips a later file's env-only
    name from "fallback" to "exported". Contract:
    test_credential_validator_cooldown_isolation.py.
    """
    exported = {
        module: set(module._SESSION_EXPORTED)
        for module in _credential_route_modules()
    }
    for module in exported:
        module._validator_last_run.clear()
    yield
    # The app mounts the `api.routes.credentials` instance, which a test's
    # first `scripts.api.server` import creates AFTER this setup ran; a
    # module absent from the snapshot was born inside the test, so its only
    # clean state is empty.
    for module in _credential_route_modules():
        module._validator_last_run.clear()
        module._SESSION_EXPORTED.clear()
        module._SESSION_EXPORTED.update(exported.get(module, set()))


@pytest.fixture(autouse=True)
def _isolate_flow_reports_dir(tmp_path, monkeypatch):
    """Point the flow-report cache at tmp_path for every test in this subtree.

    `server._FLOW_REPORTS_DIR` is the REAL `data/flow_reports`, so any case
    that reaches POST /flow-analysis/{ticker} writes a live cache entry into
    the working tree. That dirties the tree the weekend loops require clean,
    trips deploy.sh's tracked-drift guard, and seeds a stub report that both
    the FastAPI GET handler and web/app/api/flow-analysis/[ticker]/route.ts
    then serve for a real ticker. scripts/tests/test_api_flow_cache.py already
    patches this per-case; this makes it the default for the api subtree. T-275.
    """
    from scripts.api import server

    monkeypatch.setattr(server, "_FLOW_REPORTS_DIR", tmp_path / "flow_reports")
    yield
