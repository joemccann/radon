"""Regression: FastAPI error responses must NEVER echo secrets to the caller.

This is the test class for the "/health leaked IB account IDs" incident
(`feedback_health_endpoint_public_leak_and_trust_chokepoint.md`). That bug
shipped real IB account IDs + auth/connection state to anonymous public callers.
The same hazard exists anywhere a route interpolates an upstream exception /
subprocess stderr / `result.error` straight into an `HTTPException` detail: if
the upstream failure message happens to carry a Turso URL, an `auth_token=...`,
or an account id, that secret rides out in the 4xx/5xx response body.

`server.py` has ~30 such interpolating error paths (`str(exc)`, `result.error`,
`result.data["error"]`, `last_error.args[0]`). We drive a representative set
into their error branch with an upstream that raises/returns a message
CONTAINING a fake secret, then assert the response body does NOT contain the
secret.

If any assertion here fails, a route is ACTUALLY leaking — that is a finding for
the orchestrator, NOT something to paper over by weakening the assertion.

Routes are reached through the trusted-local stub (auth fails closed on unset
JWKS now); see `test_route_authz_matrix.py` for the canonical fixture.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# --- Fake secrets. None of these are real; they only need to look like the
# real thing so a verbatim echo is unambiguous. ---------------------------
FAKE_TURSO_URL = "libsql://radon-leaktest.aws-us-west-2.turso.io"
FAKE_AUTH_TOKEN = "auth_token=eyJSEKRETleaktoken1234567890"  # noqa: S105 (test literal)
FAKE_ACCOUNT_ID = "U7654321"
# A combined message that mimics a real libsql/connection traceback string.
SECRET_BLOB = (
    f"sqlite3.OperationalError: failed to connect to {FAKE_TURSO_URL} "
    f"with {FAKE_AUTH_TOKEN}; account {FAKE_ACCOUNT_ID} unauthorized"
)
SECRET_NEEDLES = (FAKE_TURSO_URL, FAKE_AUTH_TOKEN, FAKE_ACCOUNT_ID, "SEKRET")

# --- KNOWN LEAK (finding for the orchestrator) ----------------------------
# Every route exercised below CURRENTLY echoes the upstream failure message
# verbatim into its HTTPException detail. If that message carries a Turso URL /
# auth token / account id, it rides out in the response body — the exact
# /health-account-id leak class, just on the error paths instead of /health.
#
# Per the stream brief we do NOT patch server.py here; we PIN the leak with a
# strict xfail. The assertion is NOT weakened — it still demands a scrubbed
# body. strict=True means: the moment server.py is fixed to scrub these details,
# the test XPASSes and pytest turns that into a FAILURE, forcing whoever lands
# the fix to drop the xfail. That keeps the invariant honest while the suite is
# green today. Each route is marked individually so a partial fix flips only
# the routes it actually scrubbed.
LEAKS_TODAY = pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING: route echoes upstream exception/stderr/result.error verbatim "
        "into HTTPException detail; a Turso URL / auth_token / account id in "
        "that message leaks to the caller. Scrub the detail in server.py, then "
        "remove this xfail."
    ),
)


@dataclass
class _FakeScriptResult:
    """Mimics scripts.api.subprocess.ScriptResult / RawScriptResult enough for
    the route error branches: `.ok`, `.error`, `.data`."""

    ok: bool
    error: Optional[str] = None
    data: Optional[object] = None


@pytest.fixture
def trusted_client(monkeypatch):
    """A TestClient whose requests are treated as trusted-local so they reach
    the handler — we are testing the HANDLER's error rendering, not the
    perimeter. (The perimeter is covered by test_auth_fail_closed /
    test_route_authz_matrix.)"""
    from scripts.api import server, auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    from scripts.api.server import app

    return TestClient(app)


def _assert_no_secret(resp, *, route: str):
    """Assert neither status-code framing nor the JSON body echoes any secret.

    Checks the raw response text (covers detail strings AND any nested dict
    detail that radonFetch would later stringify) plus the parsed `detail`.
    """
    body_text = resp.text
    for needle in SECRET_NEEDLES:
        assert needle not in body_text, (
            f"SECRET LEAK on {route}: response body echoed {needle!r}.\n"
            f"status={resp.status_code} body={body_text!r}\n"
            "This is the /health-account-id leak class. Do NOT weaken this "
            "assertion — fix the route to scrub the secret before it reaches "
            "the HTTPException detail."
        )
    # Also assert it errored at all (we drove it into a failure branch); a 200
    # would mean the mock didn't take and the test proved nothing.
    assert resp.status_code >= 400, (
        f"{route} did not enter its error branch (status {resp.status_code}); "
        "the leak path was not exercised."
    )


class TestNoSecretLeakageFastAPI:
    @LEAKS_TODAY
    def test_internals_skew_history_str_exc(self, trusted_client, monkeypatch):
        """`/internals/skew-history` interpolates `str(exc)` (server.py:1843).
        A generic upstream exception carrying a secret must be scrubbed."""
        from scripts.api import server

        monkeypatch.setattr(server, "uw_available", True)

        async def _boom(*args, **kwargs):
            raise RuntimeError(SECRET_BLOB)

        monkeypatch.setattr(server, "_fetch_risk_reversal_history", _boom)
        # Bypass any cache so we hit the live-fetch branch.
        monkeypatch.setattr(server, "_read_internals_skew_cache", lambda path: None)

        resp = trusted_client.get("/internals/skew-history")
        _assert_no_secret(resp, route="GET /internals/skew-history")

    @LEAKS_TODAY
    def test_skew_history_helper_last_error_args(self, trusted_client, monkeypatch):
        """`_fetch_risk_reversal_history` re-raises `last_error.args[0]`
        verbatim (server.py:863). A UWAPIError message with a secret must be
        scrubbed before it reaches the HTTPException detail."""
        from scripts.api import server
        from scripts.clients.uw_client import UWAPIError

        monkeypatch.setattr(server, "uw_available", True)
        monkeypatch.setattr(server, "_read_internals_skew_cache", lambda path: None)

        class _LeakyClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_historical_risk_reversal_skew(self, *args, **kwargs):
                raise UWAPIError(SECRET_BLOB)

        monkeypatch.setattr(server, "UWClient", _LeakyClient)

        # Force the helper down the "had candidates, all failed" path so it
        # raises with last_error.args[0]. _resolve_expiry_candidates returns
        # (ib_candidates, uw_candidates, expiry_source).
        async def _candidates(*args, **kwargs):
            return ([], ["2026-12-18"], "uw")

        monkeypatch.setattr(server, "_resolve_expiry_candidates", _candidates)

        resp = trusted_client.get("/internals/skew-history")
        _assert_no_secret(resp, route="GET /internals/skew-history (helper)")

    @LEAKS_TODAY
    def test_flow_analysis_get_str_exc(self, trusted_client, monkeypatch, tmp_path):
        """`GET /flow-analysis/{ticker}` interpolates `str(exc)` from a failed
        cache read (server.py:1118)."""
        from scripts.api import server

        cache_path = tmp_path / "AAPL.json"
        cache_path.write_text("{}")
        monkeypatch.setattr(server, "_FLOW_REPORTS_DIR", tmp_path)

        def _leaky_read(*args, **kwargs):
            raise OSError(SECRET_BLOB)

        # json.loads is what runs on the file contents; make it raise the secret.
        monkeypatch.setattr(server.json, "loads", _leaky_read)

        resp = trusted_client.get("/flow-analysis/AAPL")
        _assert_no_secret(resp, route="GET /flow-analysis/{ticker}")

    @LEAKS_TODAY
    def test_flow_analysis_post_result_error(self, trusted_client, monkeypatch):
        """`POST /flow-analysis/{ticker}` echoes `result.error` (server.py:1130).
        Subprocess stderr (where a libsql crash prints the Turso URL) lands
        here verbatim."""
        from scripts.api import server

        async def _fake_run(name, args, timeout=120):
            return _FakeScriptResult(ok=False, error=SECRET_BLOB)

        monkeypatch.setattr(server, "run_script", _fake_run)

        resp = trusted_client.post("/flow-analysis/AAPL")
        _assert_no_secret(resp, route="POST /flow-analysis/{ticker}")

    @LEAKS_TODAY
    def test_flow_analysis_post_data_error(self, trusted_client, monkeypatch):
        """`POST /flow-analysis/{ticker}` also echoes `result.data['error']`
        (server.py:1134)."""
        from scripts.api import server

        async def _fake_run(name, args, timeout=120):
            return _FakeScriptResult(ok=True, data={"error": SECRET_BLOB})

        monkeypatch.setattr(server, "run_script", _fake_run)

        resp = trusted_client.post("/flow-analysis/AAPL")
        _assert_no_secret(resp, route="POST /flow-analysis/{ticker} (data.error)")

    @LEAKS_TODAY
    def test_attribution_result_error(self, trusted_client, monkeypatch):
        """`GET /attribution` echoes `result.error` (server.py:1205)."""
        from scripts.api import server

        async def _fake_run(name, args, timeout=15):
            return _FakeScriptResult(ok=False, error=SECRET_BLOB)

        monkeypatch.setattr(server, "run_script", _fake_run)

        resp = trusted_client.get("/attribution")
        _assert_no_secret(resp, route="GET /attribution")

    @LEAKS_TODAY
    def test_portfolio_sync_result_error(self, trusted_client, monkeypatch):
        """`POST /portfolio/sync` echoes `result.error` (server.py:1230).
        This path runs an IB subprocess whose stderr can contain the Turso URL
        / account id on a libsql or connection failure."""
        from scripts.api import server

        async def _fake_run(*args, **kwargs):
            return _FakeScriptResult(ok=False, error=SECRET_BLOB)

        monkeypatch.setattr(server, "_run_ib_script_with_recovery", _fake_run)

        resp = trusted_client.post("/portfolio/sync")
        _assert_no_secret(resp, route="POST /portfolio/sync")

    @LEAKS_TODAY
    def test_portfolio_sync_read_back_str_exc(self, trusted_client, monkeypatch):
        """`POST /portfolio/sync` interpolates `str(e)` from the portfolio
        read-back (server.py:1237)."""
        from scripts.api import server

        async def _fake_run(*args, **kwargs):
            return _FakeScriptResult(ok=True, error=None)

        monkeypatch.setattr(server, "_run_ib_script_with_recovery", _fake_run)

        import utils.atomic_io as atomic_io

        def _leaky_load(*args, **kwargs):
            raise RuntimeError(SECRET_BLOB)

        monkeypatch.setattr(atomic_io, "verified_load", _leaky_load)

        resp = trusted_client.post("/portfolio/sync")
        _assert_no_secret(resp, route="POST /portfolio/sync (read-back)")
