"""CORS origin allowlist (2026-06-29 security audit).

The CORS policy was a `https://.*\\.radon\\.run` wildcard regex, which matched
ANY *.radon.run subdomain — a subdomain takeover of a stale host would have
passed CORS. It is now an explicit allowlist. These tests pin that:

  1. A legitimate origin (app.radon.run) gets an Access-Control-Allow-Origin.
  2. An arbitrary *.radon.run subdomain (the takeover case) does NOT.
  3. No `allow_origin_regex` wildcard remains configured on the app.

TestClient is used WITHOUT a `with` block so the app lifespan (IB pool) never
starts. /health is auth-exempt, so a plain GET is a clean CORS probe carrier.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _client() -> TestClient:
    from scripts.api.server import app

    return TestClient(app)


def test_allowlisted_origin_gets_cors_header():
    res = _client().get("/health", headers={"Origin": "https://app.radon.run"})
    assert res.headers.get("access-control-allow-origin") == "https://app.radon.run"


def test_arbitrary_radon_subdomain_is_rejected():
    # The subdomain-takeover case the wildcard regex used to allow.
    res = _client().get("/health", headers={"Origin": "https://evil.radon.run"})
    assert "access-control-allow-origin" not in res.headers


def test_no_wildcard_origin_regex_configured():
    from scripts.api.server import app

    for mw in app.user_middleware:
        if mw.cls.__name__ == "CORSMiddleware":
            opts = getattr(mw, "kwargs", {}) or {}
            assert opts.get("allow_origin_regex") is None, (
                "CORS must use an explicit allow_origins list, not a wildcard regex"
            )
            assert "https://app.radon.run" in (opts.get("allow_origins") or [])
            return
    raise AssertionError("CORSMiddleware not found on app")
