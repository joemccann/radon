"""Least-privilege scope for the demo frontend's service principal.

`RADON_SERVICE_TOKEN` (header `X-Radon-Service-Token`) lets the demo frontend
authenticate to the demo VM's FastAPI without forwarding a per-user JWT. It is
consumed inside `verify_clerk_jwt`, so the middleware takes its verdict as the
global one: before this scope existed, a holder of that single shared machine
credential reached EVERY non-exempt route, including the order, trading, exec,
credential and admin sinks -- and because the lane short-circuits before
`verify_clerk_bearer`, it also skipped `ALLOWED_USER_IDS` /
`RADON_REQUIRE_OPERATOR_ALLOWLIST`.

The repository already scopes its other machine credential this way
(`API_KEY_ALLOWED_PATHS`: "API key cannot access trading/order endpoints"), so
this is the same control applied to the credential with the wider reach.

Strategy mirrors `test_route_authz_matrix.py`: enumerate `app.routes` at
runtime so a brand-new privileged route is automatically in scope, and pin the
reviewed privileged set with set-equality so adding or renaming one forces a
deliberate update here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SERVICE_TOKEN = "service-token-scope-probe"

_PATH_PARAM_PLACEHOLDERS = {
    "ticker": "AAPL",
    "symbol": "AAPL",
    "unit": "radon-api",
    "action": "status",
    "post_id": "1",
    "service_id": "ib_gateway",
    "name": "TWS_PASSWORD",
    "key": "RADON_MAX_ORDER_NOTIONAL",
    "strategy": "theta",
    "operation": "chat",
}

# Every route a demo-frontend service principal must NOT reach, reviewed one by
# one. These are the privileged sinks: live order admission, the trading halt,
# the arbitrary-script bridge, the credential store, the service/host controls,
# Gateway lifecycle, journal mutation, live portfolio sync, the preference
# writes that tune the order-notional caps, and the metered third-party lanes
# (UW quota, the TradingView MCP) that no demo route proxies and that a stolen
# token would otherwise spend. Read surfaces the demo actually proxies (see
# web/lib/demo/routePolicy.ts "backend" endpoints) stay reachable.
#
# This set is reviewed by hand, so it cannot see a privileged route nobody
# added to it: such a route sits under no denied prefix, both sides of the
# set-equality pin below agree, and the gap ships green. Scope blind spots on
# the metered lanes are therefore caught from the handler side instead, by
# scripts/api/tests/test_metered_client_scope.py.
_REVIEWED_PRIVILEGED = frozenset({
    "/admin/logs",
    "/admin/services",
    "/admin/services/{unit}/{action}",
    "/admin/stack/restart",
    "/credentials",
    "/credentials/{service_id}",
    "/credentials/{service_id}/validate",
    "/credentials/{service_id}/{name}",
    "/ib/operator-hold",
    "/ib/reset-backoff",
    "/ib/restart",
    "/journal/reconcile",
    "/journal/rehydrate",
    "/model-gateway/{operation}",
    "/options/uw-chain",
    "/orders/cancel",
    "/orders/cancel-all",
    "/orders/modify",
    "/orders/place",
    "/orders/refresh",
    "/orders/replace",
    "/orders/whatif",
    "/pi/exec",
    "/portfolio/background-sync",
    "/portfolio/sync",
    "/preferences/{key}",
    "/quote/{ticker}",
    "/research/tradingview/call",
    "/research/tradingview/ohlcv",
    "/research/tradingview/status",
    "/research/tradingview/symbols",
    "/trading/halt",
    "/trading/kill",
    "/trading/resume",
    "/trading/status",
    "/uw/capabilities",
    "/uw/request",
    "/uw/research",
    "/uw/research/events",
    "/uw/research/refresh",
    "/uw/usage",
    "/uw/usage/record",
})

# Routes the demo frontend genuinely proxies with this credential. They must
# still be ADMITTED, or the scope has broken the deployment it exists to serve.
# POST bodies are deliberately absent: a 4xx from the handler's own validation
# still proves the request passed the perimeter, which is what is under test.
_MUST_STAY_REACHABLE = (
    ("GET", "/health/lite"),
    ("GET", "/preferences"),
    ("POST", "/paper/place"),
    ("POST", "/knowledge/search"),
    ("GET", "/llm-token-index"),
)

_DENIED_STATUSES = {401, 403}


def _materialize_path(template: str) -> str:
    resolved = template
    for name, value in _PATH_PARAM_PLACEHOLDERS.items():
        resolved = resolved.replace("{" + name + "}", value)
    return resolved


def _iter_app_routes():
    from scripts.api.server import app

    for route in app.routes:
        if not isinstance(route, Route):
            continue
        verbs = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        for verb in verbs:
            yield verb, route.path


@pytest.fixture
def service_token_client(monkeypatch):
    """A TestClient whose every request arrives as the demo frontend: public
    peer (no loopback bypass), no API key, JWKS configured, and a VALID
    service token in the header."""
    from scripts.api import server, auth

    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    monkeypatch.setenv("RADON_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("RADON_API_TEST_MODE", "1")
    monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "verify_api_key", lambda request: None)

    # These cases assert the perimeter REFUSES the request, so the handler is
    # never supposed to run. Stub its outbound sinks anyway: when the scope
    # regresses, the assertion must fail on the status code, not by letting a
    # test suite make a live third-party call on the way to failing.
    _inert_third_party_clients(monkeypatch)

    from scripts.api.server import app

    return TestClient(app, headers={"X-Radon-Service-Token": SERVICE_TOKEN})


def _inert_third_party_clients(monkeypatch):
    """Make every metered/third-party egress sink behind these routes inert."""

    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "A third-party client was constructed while testing the service-token "
            "scope. The route under test must be refused at the perimeter; a "
            "handler that runs here means the scope regressed."
        )

    from clients import uw_client

    monkeypatch.setattr(uw_client, "UWClient", _refuse)
    monkeypatch.delenv("UW_TOKEN", raising=False)

    from api.routes import assistant_market

    monkeypatch.setattr(assistant_market, "UWClient", _refuse)
    monkeypatch.setattr(assistant_market, "fetch_yahoo_last", lambda ticker: None)

    from api.routes import tradingview

    monkeypatch.setattr(tradingview, "_client", _refuse)
    from api.routes import model_gateway
    monkeypatch.setattr(model_gateway.gateway, "request", _refuse)


_PRIVILEGED_CALLS = sorted(
    (verb, path)
    for verb, path in _iter_app_routes()
    if path in _REVIEWED_PRIVILEGED
)


class TestServiceTokenIsScoped:
    @pytest.mark.parametrize(("method", "path"), [
        ("POST", "/model-gateway/chat"),
        ("POST", "/model-gateway/json"),
        ("POST", "/model-gateway/models"),
        ("POST", "/model-gateway/future-inference"),
        ("GET", "/model-gateway/status"),
    ])
    def test_gateway_inference_never_dispatches_for_demo_token(self, service_token_client, monkeypatch, method, path):
        from api.routes import model_gateway
        calls=[]
        monkeypatch.setattr(model_gateway.gateway,'request',lambda *a,**k:calls.append(a) or {'status':'ok'})
        response=service_token_client.request(method,path,json={})
        assert response.status_code in _DENIED_STATUSES
        assert calls==[]

    def test_demo_catalog_get_is_the_only_gateway_exception(self, service_token_client, monkeypatch):
        from api.routes import model_gateway
        calls=[]
        catalog={'models':[],'defaultId':'','source':'gateway'}
        monkeypatch.setattr(model_gateway.gateway,'request',lambda *a,**k:calls.append(a) or catalog)
        response=service_token_client.get('/model-gateway/models')
        assert response.status_code==200 and response.json()==catalog
        assert calls==[('models',{})]

    def test_token_is_actually_accepted_by_the_lane(self, service_token_client):
        """Control: the probe token must really authenticate, or every denial
        below would be vacuous (a rejected token denies everything)."""
        from scripts.api import auth

        class _Url:
            path = "/llm-token-index"

        class _Req:
            headers = {"X-Radon-Service-Token": SERVICE_TOKEN}
            url = _Url()

        assert auth.is_trusted_service_request(_Req()) is True

    def test_lane_refuses_a_valid_token_on_a_privileged_path(self, service_token_client):
        """The scope lives in the lane itself, not only in the middleware, so a
        route that takes verify_clerk_jwt as a Depends() is covered too."""
        from scripts.api import auth

        class _Url:
            path = "/orders/place"

        class _Req:
            headers = {"X-Radon-Service-Token": SERVICE_TOKEN}
            url = _Url()

        assert auth.is_trusted_service_request(_Req()) is False

    @pytest.mark.parametrize(
        ("method", "path"),
        _PRIVILEGED_CALLS,
        ids=[f"{m} {p}" for m, p in _PRIVILEGED_CALLS],
    )
    def test_privileged_route_denies_the_service_principal(
        self, service_token_client, method, path
    ):
        response = service_token_client.request(method, _materialize_path(path))
        assert response.status_code in _DENIED_STATUSES, (
            f"{method} {path} returned {response.status_code} for the shared "
            "demo service token. A machine credential with no per-user identity "
            "must not reach a privileged sink; scope it in "
            "auth.SERVICE_TOKEN_DENIED_PREFIXES."
        )

    @pytest.mark.parametrize(("method", "path"), _MUST_STAY_REACHABLE)
    def test_demo_surface_still_reachable(self, service_token_client, method, path):
        kwargs = {"json": {}} if method == "POST" else {}
        response = service_token_client.request(method, path, **kwargs)
        assert response.status_code not in _DENIED_STATUSES, (
            f"{method} {path} is a surface the demo frontend proxies with this "
            f"credential but it was denied ({response.status_code}) — the scope "
            "is too narrow."
        )


# Starlette normalizes and percent-decodes the path BEFORE routing, so a prefix
# check has to read the same string the router does. These shapes all resolve to
# a route in the denied set, so each one must be refused; they are pinned
# because a prefix check written against the literal path would let them past.
_NORMALIZED_PRIVILEGED_SHAPES = (
    ("POST", "/orders/place"),
    ("POST", "/./orders/place"),
    ("POST", "/x/../orders/place"),
    ("POST", "/%6frders/place"),
    ("POST", "/orders%2fplace"),
    ("POST", "/orders/./place"),
    ("POST", "/pi/exec"),
    ("POST", "/pi/./exec"),
    ("POST", "/trading/kill"),
    ("POST", "/trading/kill/"),
    ("GET", "/credentials"),
    ("GET", "/credentials/"),
    ("POST", "/admin/stack/restart"),
    ("PUT", "/preferences/RADON_MAX_ORDER_NOTIONAL"),
    # The metered lanes that sit on their own path rather than under /uw/.
    ("GET", "/options/uw-chain"),
    ("GET", "/options/uw-chain?symbol=AAPL"),
    ("GET", "/./options/uw-chain"),
    ("GET", "/x/../options/uw-chain"),
    ("GET", "/options/%75w-chain"),
    ("GET", "/quote/AAPL"),
    ("GET", "/quote/./AAPL"),
    ("GET", "/x/../quote/AAPL"),
    ("GET", "/%71uote/AAPL"),
)


class TestScopeSurvivesPathNormalization:
    @pytest.mark.parametrize(
        ("method", "path"),
        _NORMALIZED_PRIVILEGED_SHAPES,
        ids=[f"{m} {p}" for m, p in _NORMALIZED_PRIVILEGED_SHAPES],
    )
    def test_normalized_shape_is_still_denied(
        self, service_token_client, method, path
    ):
        """A prefix check is only as good as the string it reads. `url.path`
        carries the decoded, normalized path, so a dot segment or a %2f cannot
        route to a privileged handler while presenting an innocent prefix."""
        kwargs = {"json": {}} if method in {"POST", "PUT"} else {}
        response = service_token_client.request(method, path, **kwargs)
        assert response.status_code in _DENIED_STATUSES, (
            f"{method} {path} returned {response.status_code}; this shape "
            "normalizes to a privileged route and must be refused."
        )


class TestPrivilegedSetPinned:
    """The deny prefixes and the reviewed route set must describe each other
    exactly. A new privileged route that lands under an existing prefix, or one
    that needs a new prefix, fails here instead of shipping unscoped."""

    def test_prefixes_select_exactly_the_reviewed_routes(self):
        from scripts.api.auth import SERVICE_TOKEN_DENIED_PREFIXES

        selected = {
            path
            for _verb, path in _iter_app_routes()
            if path.startswith(tuple(SERVICE_TOKEN_DENIED_PREFIXES))
        }
        assert selected == set(_REVIEWED_PRIVILEGED), (
            "SERVICE_TOKEN_DENIED_PREFIXES and _REVIEWED_PRIVILEGED disagree. "
            f"only matched by the prefixes: {sorted(selected - _REVIEWED_PRIVILEGED)}; "
            f"only in the reviewed set: {sorted(_REVIEWED_PRIVILEGED - selected)}. "
            "Every privileged FastAPI route is a scope decision — update both in "
            "the same reviewed change."
        )

    def test_reviewed_set_is_non_trivial(self):
        assert len(_PRIVILEGED_CALLS) >= 25, (
            f"Expected the privileged call matrix to enumerate many routes; got "
            f"{len(_PRIVILEGED_CALLS)}. Do not delete this guard — an empty "
            "matrix passes vacuously."
        )
