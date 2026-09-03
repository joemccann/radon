"""Every live FastAPI (method, path) must be pinned with a chat capability."""

from __future__ import annotations

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
            "/gamma-rotation/scan",
            "/bpi/scan",
        )
        for path in scans:
            assert capability_for("POST", path) == "read.spawn", path

    def test_backtest_refresh_is_a_mutation_not_a_read(self, monkeypatch):
        """refresh spawns a 180s subprocess that persists to Turso: it must
        not be reachable through the GET pinned as a plain read."""
        from scripts.api import auth, server
        from scripts.api.assistant_catalog import capability_for

        assert capability_for("GET", "/backtest/{strategy}") == "read"
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
