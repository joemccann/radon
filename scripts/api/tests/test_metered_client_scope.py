"""Every route that spends a metered third-party credential is a scope decision.

`SERVICE_TOKEN_DENIED_PREFIXES` is a path denylist, and
`test_service_token_scope.py` pins it against a hand-reviewed privileged set.
That pin cannot see a privileged route nobody added to either side: a route
under no denied prefix is simply never selected, so both sides agree and the
gap passes CI. Two UW-metered assistant lanes shipped that way.

This file closes that blind spot from the other direction, without reading the
denylist to decide what to look at. It derives the metered surface from the
handlers themselves -- every FastAPI route whose handler reaches the Unusual
Whales client or the TradingView MCP bridge, directly or through a helper in
the same module -- and requires each one to be EITHER denied to the demo
service principal OR declared as a `backend` endpoint the demo frontend
actually proxies (`web/lib/demo/routePolicy.ts`). A new metered route that is
neither forces a decision here.

`UW_TOKEN` is a required key on every Radon host, so the demo VM carries a
live, account-shared credential and UW quota is account-wide: a metered route
reachable by the shared machine credential spends the operator's production
quota.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent
ROUTE_POLICY_TS = REPO_ROOT / "web" / "lib" / "demo" / "routePolicy.ts"

# Modules whose import graph bills a third-party account per call.
METERED_CLIENT_MODULES = ("clients.uw_client", "clients.tradingview_client")

_HTTP_VERBS = {"get", "post", "put", "patch", "delete"}


def _api_source_files() -> list[Path]:
    files = [API_DIR / "server.py"]
    files.extend(sorted((API_DIR / "routes").glob("*.py")))
    return [path for path in files if path.is_file()]


def _metered_names(tree: ast.AST) -> set[str]:
    """Names bound in this module from a metered client module, including the
    function-local `from clients.uw_client import UWClient` form."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in METERED_CLIENT_MODULES:
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in METERED_CLIENT_MODULES:
                    names.add((alias.asname or alias.name).split(".")[0])
    return names


def _router_prefix(tree: ast.AST) -> str:
    """`APIRouter(prefix="/research/tradingview")` moves every path in the
    module; a prefix check reads the mounted path, not the decorator's."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "APIRouter":
            continue
        for keyword in node.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    return ""


def _referenced_names(node: ast.AST) -> set[str]:
    """Every bare name and called attribute root inside a function body."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, (ast.ImportFrom, ast.Import)):
            for alias in child.names:
                found.add(alias.asname or alias.name)
    return found


def _route_decorations(node: ast.AST) -> list[tuple[str, str]]:
    """(verb, path) for each `@app.get("/x")` / `@router.post("/x")`."""
    routes: list[tuple[str, str]] = []
    for decorator in getattr(node, "decorator_list", []):
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Attribute) or func.attr not in _HTTP_VERBS:
            continue
        holder = getattr(func.value, "id", "")
        if holder not in {"app", "router"}:
            continue
        if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
            continue
        routes.append((func.attr.upper(), str(decorator.args[0].value)))
    return routes


def _metered_routes_in(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    metered = _metered_names(tree)
    if not metered:
        return set()
    prefix = _router_prefix(tree)

    functions: dict[str, ast.AST] = {}
    references: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
            references[node.name] = _referenced_names(node)

    # A handler is metered when it touches a metered name, or calls a function
    # in this module that does. Iterate to a fixed point so a two-hop helper
    # chain (handler -> _fetch -> UWClient) still counts.
    reaching = {name for name, refs in references.items() if refs & metered}
    changed = True
    while changed:
        changed = False
        for name, refs in references.items():
            if name not in reaching and refs & reaching:
                reaching.add(name)
                changed = True

    routes: set[tuple[str, str]] = set()
    for name in reaching:
        for verb, decorated in _route_decorations(functions[name]):
            routes.add((verb, f"{prefix}{decorated}"))
    return routes


def _metered_routes() -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in _api_source_files():
        found |= _metered_routes_in(path)
    return sorted(found)


def _proxied_backend_endpoints() -> set[str]:
    """The `backend` endpoint strings in routePolicy.ts: the only FastAPI paths
    a demo route actually proxies with the service credential."""
    source = ROUTE_POLICY_TS.read_text(encoding="utf-8")
    endpoints: set[str] = set()
    for block in re.findall(r"endpoints:\s*\[([^\]]*)\]", source):
        endpoints.update(re.findall(r'"([^"]+)"', block))
    return {value for value in endpoints if value.startswith("/")}


METERED_ROUTES = _metered_routes()


def _is_denied(path: str) -> bool:
    from scripts.api.auth import SERVICE_TOKEN_DENIED_PREFIXES

    return path.startswith(tuple(SERVICE_TOKEN_DENIED_PREFIXES))


def _is_proxied(path: str, endpoints: set[str]) -> bool:
    for endpoint in endpoints:
        if endpoint.endswith("/"):
            if path.startswith(endpoint):
                return True
        elif path == endpoint:
            return True
    return False


class TestMeteredRoutesAreScoped:
    def test_analysis_found_the_metered_surface(self):
        """Guard the guard: an analyzer that silently stops matching passes
        every assertion below vacuously."""
        assert len(METERED_ROUTES) >= 6, (
            "The metered-route analysis found "
            f"{len(METERED_ROUTES)} routes ({METERED_ROUTES}). It is meant to "
            "find the UW and TradingView handlers; if the import or decorator "
            "shape changed, fix the analysis rather than deleting this guard."
        )

    @pytest.mark.parametrize(
        ("verb", "path"),
        METERED_ROUTES,
        ids=[f"{verb} {path}" for verb, path in METERED_ROUTES],
    )
    def test_metered_route_is_denied_or_proxied(self, verb, path):
        endpoints = _proxied_backend_endpoints()
        denied = _is_denied(path)
        proxied = _is_proxied(path, endpoints)
        assert denied or proxied, (
            f"{verb} {path} spends a metered third-party credential and is "
            "reachable by the shared demo service token, but no demo route "
            "proxies it (not a `backend` endpoint in "
            "web/lib/demo/routePolicy.ts). A machine credential must not be "
            "able to spend the operator's production quota on a lane the demo "
            "does not use: add the path to "
            "auth.SERVICE_TOKEN_DENIED_PREFIXES, or declare the demo route "
            "that proxies it."
        )

    @pytest.mark.parametrize(
        ("verb", "path"),
        METERED_ROUTES,
        ids=[f"{verb} {path}" for verb, path in METERED_ROUTES],
    )
    def test_metered_route_is_not_both(self, verb, path):
        """A denied path that a demo route also proxies is a broken deployment,
        not a tighter scope."""
        endpoints = _proxied_backend_endpoints()
        assert not (_is_denied(path) and _is_proxied(path, endpoints)), (
            f"{verb} {path} is denied to the service principal AND declared as "
            "a proxied `backend` endpoint in routePolicy.ts. The demo route "
            "will 401; reconcile the two."
        )
