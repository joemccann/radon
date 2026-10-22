"""Every live FastAPI (method, path) must be pinned with a chat capability."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.routing import Route

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _iter_app_routes():
    from scripts.api.server import app

    for route in app.routes:
        if not isinstance(route, Route):
            continue
        verbs = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        for verb in verbs:
            yield verb, route.path


class TestAssistantCatalog:
    def test_every_live_route_is_pinned(self):
        from scripts.api.assistant_catalog import CATALOG, capability_for

        live = sorted(_iter_app_routes())
        assert len(live) >= 40, (
            f"Expected a non-trivial FastAPI surface; got {len(live)}"
        )

        unclassified = [
            (method, path)
            for method, path in live
            if capability_for(method, path) is None
        ]
        assert unclassified == [], (
            "unclassified FastAPI routes must be pinned in "
            f"assistant_catalog.CATALOG: {unclassified}"
        )

        live_set = set(live)
        stale = sorted(key for key in CATALOG if key not in live_set)
        assert stale == [], f"catalog pins missing from app.routes: {stale}"

    def test_named_pins(self):
        from scripts.api.assistant_catalog import capability_for, is_refused

        assert capability_for("POST", "/orders/place") == "mutate.trading"
        assert capability_for("POST", "/pi/exec") == "internal"
        assert capability_for("GET", "/quote/{ticker}") == "read"
        assert is_refused("mutate.trading")
        assert is_refused("internal")
        assert is_refused("admin")
        assert not is_refused("read")
        assert not is_refused("read.spawn")
        assert not is_refused("mutate.workspace")

    def test_admin_ib_trading_prefixes_are_admin(self):
        from scripts.api.assistant_catalog import CATALOG

        for (method, path), cap in CATALOG.items():
            if (
                path.startswith("/admin")
                or path.startswith("/ib/")
                or path.startswith("/trading")
            ):
                assert cap == "admin", f"{method} {path} must be admin"

    def test_order_risk_cap_preferences_are_refused_to_the_assistant(self):
        """/preferences is the order-risk cap surface, not workspace state.

        scripts/app_preferences.py registers RADON_MAX_ORDER_NOTIONAL,
        RADON_MAX_ORDER_QTY, RADON_MAX_ORDERS_PER_MIN and
        RADON_MAX_COMBO_LOSS_DOLLARS there, each with a hard_max far above its
        default. A capability the assistant may call is reachable from
        prompt-injectable content, so widening the placement funnel's own caps
        must be refused the same way /credentials is.
        """
        from scripts.api.assistant_catalog import capability_for, is_refused

        for method, path in (
            ("GET", "/preferences"),
            ("PUT", "/preferences/{key}"),
            ("DELETE", "/preferences/{key}"),
        ):
            cap = capability_for(method, path)
            assert cap == "admin", f"{method} {path} pinned {cap!r}, must be admin"
            assert is_refused(cap), f"{method} {path} must be refused to the assistant"

    def test_scan_posts_are_read_spawn(self):
        from scripts.api.assistant_catalog import capability_for

        scans = (
            "/scan",
            "/discover",
            "/gex/scan",
            "/vcg/scan",
            "/regime/scan",
            "/breadth/scan",
            "/leap/scan",
            "/garch-convergence/scan",
            "/theta-harvester/scan",
            "/strength-confirmation/scan",
            "/vol-skew-mr/scan",
            "/bounce-setup/scan",
            "/gamma-rotation/scan",
            "/bpi/scan",
        )
        for path in scans:
            assert capability_for("POST", path) == "read.spawn", path

    def test_routes_that_run_a_script_are_never_plain_reads(self):
        """A plain read skips MAX_SPAWN_PER_TURN in the chat dispatcher, so a
        handler that spawns a subprocess must be pinned read.spawn or
        stricter (REL-177 class)."""
        from scripts.api.assistant_catalog import capability_for

        api_dir = SCRIPTS_DIR / "api"
        sources = [api_dir / "server.py", *sorted((api_dir / "routes").glob("*.py"))]
        spawning = []
        for source in sources:
            funcs = [
                node
                for node in ast.walk(ast.parse(source.read_text()))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            calls = {
                node: {
                    call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                }
                for node in funcs
            }
            # A handler that spawns through a module helper spawns too.
            spawners = {"run_script", "run_script_raw"}
            while True:
                grown = spawners | {node.name for node in funcs if calls[node] & spawners}
                if grown == spawners:
                    break
                spawners = grown
            for node in funcs:
                if not calls[node] & spawners:
                    continue
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in {"get", "post", "put", "delete"}
                        and dec.args
                        and isinstance(dec.args[0], ast.Constant)
                    ):
                        spawning.append((dec.func.attr.upper(), dec.args[0].value))

        assert ("GET", "/ticker/ratings") in spawning
        assert ("GET", "/backtest/{strategy}") in spawning
        plain = [key for key in spawning if capability_for(*key) == "read"]
        assert plain == [], f"subprocess-backed routes pinned plain read: {plain}"

    def test_backtest_refresh_is_a_mutation_not_a_read(self, monkeypatch):
        """refresh spawns a 180s subprocess that persists to Turso: it must
        not be reachable through the GET pinned as a plain read."""
        from scripts.api import auth, server
        from scripts.api.assistant_catalog import capability_for

        assert capability_for("GET", "/backtest/{strategy}") == "read.spawn"
        assert (
            capability_for("POST", "/backtest/{strategy}/refresh")
            == "mutate.workspace"
        )

        monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
        spawned = []

        async def _spawn(script, args, timeout=None):
            spawned.append((script, args, timeout))
            from api.subprocess import ScriptResult

            return ScriptResult(ok=True, data={"strategy": args[1], "fresh": True})

        monkeypatch.setattr(server, "run_script", _spawn)
        monkeypatch.setattr(
            server,
            "_load_latest_backtest_run",
            lambda strategy: {"strategy": strategy, "cached": True},
        )
        client = TestClient(server.app)

        assert client.get("/backtest/vcg").json() == {"strategy": "vcg", "cached": True}
        assert client.get("/backtest/vcg?refresh=true").status_code == 400
        assert client.get("/backtest/vcg?refresh=1").status_code == 400
        assert spawned == []

        response = client.post("/backtest/vcg/refresh")
        assert response.status_code == 200
        assert response.json() == {"strategy": "vcg", "fresh": True}
        assert spawned == [("backtest_run.py", ["--strategy", "vcg", "--persist"], 180)]

    def test_unpinned_post_is_default_deny(self):
        from scripts.api.assistant_catalog import capability_for

        assert capability_for("POST", "/not-a-real-route") is None
        assert capability_for("GET", "/not-a-real-route") is None
