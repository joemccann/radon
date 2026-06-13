"""Default-deny perimeter matrix: every @app route is unreachable unauthenticated.

This is the test class that would have caught the "world-callable /api/*" incident
(feedback_middleware_is_the_perimeter / feedback_health_endpoint_public_leak): a
single route shipped without auth, reachable by any anonymous caller through the
public Caddy proxy.

Strategy — enumerate `app.routes` at runtime (NOT a hardcoded list, so a brand-new
route is automatically in scope) and, for the untrusted-caller threat model, assert
that every route OUTSIDE `AUTH_EXEMPT_PATHS` is STOPPED at the perimeter (401/403,
never 200 and never reaching its handler). The exempt paths are pinned separately so
the exemption set itself can't silently grow.

Threat model is forced via monkeypatch, matching the remote-attacker case:
  - is_trusted_local_request -> False   (arrived via the public reverse proxy)
  - verify_api_key           -> None    (no service key)
  - CLERK_JWKS_URL set        (auth IS configured; we're past the fail-closed 503)
  - verify_clerk_jwt         -> raises HTTPException(401)  (no/invalid Clerk session)

Under that model the ONLY reachable routes must be the exempt ones.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.routing import Route

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# Placeholders for path params so we can address routes like
# /flow-analysis/{ticker} or /admin/services/{unit}/{action}. The VALUE is
# irrelevant — auth runs in the middleware, before the handler, so the request
# is rejected at the perimeter regardless of whether the placeholder resolves to
# a real resource.
_PATH_PARAM_PLACEHOLDERS = {
    "ticker": "AAPL",
    "symbol": "AAPL",
    "unit": "radon-api",
    "action": "status",
    "post_id": "1",
}


def _materialize_path(template: str) -> str:
    """Substitute {param} placeholders so the route is addressable."""
    resolved = template
    for name, value in _PATH_PARAM_PLACEHOLDERS.items():
        resolved = resolved.replace("{" + name + "}", value)
    return resolved


def _iter_app_routes():
    """Yield (method, path_template) for every concrete HTTP route on the app.

    HEAD/OPTIONS are dropped (CORS/automatic), and we keep one verb per route so
    the matrix exercises every distinct path at least once.
    """
    from scripts.api.server import app

    for route in app.routes:
        if not isinstance(route, Route):
            continue
        verbs = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        if not verbs:
            continue
        for verb in verbs:
            yield verb, route.path


@pytest.fixture
def untrusted_client(monkeypatch):
    """A TestClient whose every request is treated as an anonymous remote caller.

    Mirrors the wiring in test_auth_fail_closed.py but with JWKS CONFIGURED so we
    exercise the JWT-required path (401), not the unconfigured fail-closed path
    (503). A 401/403 here proves the route is gated; a 200 would mean a hole.
    """
    from scripts.api import server, auth

    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "verify_api_key", lambda request: None)

    async def _deny_jwt(request):
        raise HTTPException(status_code=401, detail="Invalid token")

    monkeypatch.setattr(server, "verify_clerk_jwt", _deny_jwt)
    monkeypatch.setattr(auth, "verify_clerk_jwt", _deny_jwt)

    from scripts.api.server import app

    return TestClient(app)


def _non_exempt_routes():
    """Concrete (method, path) tuples for every route NOT in AUTH_EXEMPT_PATHS."""
    from scripts.api.server import AUTH_EXEMPT_PATHS

    return [
        (verb, path)
        for verb, path in _iter_app_routes()
        if path not in AUTH_EXEMPT_PATHS
    ]


# Resolve once at import so the parametrize id list is the live route surface.
_NON_EXEMPT = _non_exempt_routes()
_DENIED_STATUSES = {401, 403}


class TestDefaultDenyMatrix:
    def test_route_surface_is_non_trivial(self):
        """Guard against a refactor that makes _iter_app_routes silently empty —
        an empty matrix would make every per-route assertion vacuously pass."""
        assert len(_NON_EXEMPT) >= 40, (
            f"Expected the FastAPI route surface to enumerate many routes; got "
            f"{len(_NON_EXEMPT)}. If routing changed, update the floor — do NOT "
            "delete this guard (an empty matrix passes vacuously)."
        )

    @pytest.mark.parametrize(
        ("method", "path"),
        _NON_EXEMPT,
        ids=[f"{m} {p}" for m, p in _NON_EXEMPT],
    )
    def test_non_exempt_route_denies_anonymous_caller(
        self, untrusted_client, method, path
    ):
        """Every non-exempt route MUST reject an unauthenticated remote caller.

        A 200 means the handler ran for an anonymous caller — the world-callable
        bug. A 401/403 means the middleware stopped it at the perimeter. We assert
        the route is in the denied set rather than asserting != 200 so a redirect
        or 5xx-from-the-handler (which would mean the handler RAN) also fails.
        """
        resolved = _materialize_path(path)
        response = untrusted_client.request(method, resolved)
        assert response.status_code in _DENIED_STATUSES, (
            f"{method} {path} returned {response.status_code} for an anonymous "
            "remote caller — it must be gated (401/403). If this route is "
            "deliberately public, add it to AUTH_EXEMPT_PATHS in server.py with a "
            "security review, NOT here."
        )


class TestExemptPathsPinned:
    """Pin the exemption set so it can't silently grow. Adding a path to
    AUTH_EXEMPT_PATHS must be a deliberate, reviewed change that also updates this
    constant — exactly the discipline the web-side allowlist enforces."""

    _REVIEWED_EXEMPT = {
        "/health",            # liveness probe; trust-scoped payload (no acct IDs to public)
        "/ws-ticket/validate",  # internal ticket validation, loopback daemon
        "/docs",              # Swagger UI
        "/openapi.json",      # schema for the docs UI
    }

    def test_exempt_set_matches_reviewed_allowlist(self):
        from scripts.api.server import AUTH_EXEMPT_PATHS

        assert set(AUTH_EXEMPT_PATHS) == self._REVIEWED_EXEMPT, (
            "AUTH_EXEMPT_PATHS changed. Every auth-exempt FastAPI path is "
            "world-reachable — adding one is a security decision. Update "
            "_REVIEWED_EXEMPT here in the SAME change (with justification) so the "
            "exemption stays an explicit, reviewed allowlist."
        )

    @pytest.mark.parametrize("exempt_path", sorted(_REVIEWED_EXEMPT))
    def test_exempt_path_reachable_without_auth(
        self, untrusted_client, exempt_path
    ):
        """The exempt paths must actually be reachable by an anonymous caller —
        otherwise they aren't really exempt and the matrix above is misclassifying
        them. We only assert they are NOT denied (401/403); the handler's own
        status (200, or a body-parse error for the POST ticket validator) is out
        of scope here."""
        if exempt_path == "/ws-ticket/validate":
            # POST-only, and the handler ITSELF returns 401 for a bad ticket —
            # indistinguishable by status from a perimeter denial. Mint a real
            # ticket so a clean pass through both perimeter AND handler yields 200,
            # which is unambiguous proof the route is exempt.
            from api.ws_ticket import create_ticket

            ticket = create_ticket("authz-matrix-probe")
            response = untrusted_client.post(exempt_path, json={"ticket": ticket})
        else:
            response = untrusted_client.get(exempt_path)
        assert response.status_code not in _DENIED_STATUSES, (
            f"{exempt_path} is in AUTH_EXEMPT_PATHS but an anonymous caller was "
            f"denied ({response.status_code}); the exemption is not taking effect."
        )
