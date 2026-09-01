"""Every live FastAPI (method, path) must be pinned with a chat capability."""

from __future__ import annotations

import sys
from pathlib import Path

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

    def test_unpinned_post_is_default_deny(self):
        from scripts.api.assistant_catalog import capability_for

        assert capability_for("POST", "/not-a-real-route") is None
        assert capability_for("GET", "/not-a-real-route") is None
