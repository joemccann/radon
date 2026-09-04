"""REL-193 (R-526, R-550, R-551, R-552): the hosted MCP is bounded at every
layer — inbound body, upstream reads, JWKS lookups, and concurrency.

R-526: nothing bounded an anonymous POST body anywhere on the path; a
multi-hundred-MB body OOM-killed the 512M unit and five in a minute parked it
`start-limit-hit` forever.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from mcp_hosted import auth as mcp_auth  # noqa: E402
from mcp_hosted import server as hosted  # noqa: E402
from mcp_hosted.auth import AuthError  # noqa: E402


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


class TestCaddyBodyCap:
    def test_the_mcp_block_caps_the_request_body(self):
        src = _uncommented(_PROJECT_ROOT / "cloud" / "caddy" / "Caddyfile")
        match = re.search(r"handle /mcp\*\s*\{(.*?)\n    \}", src, re.DOTALL)
        assert match, "no handle /mcp* block found in the Caddyfile"
        assert "request_body" in match.group(1) and "max_size" in match.group(1), (
            "the /mcp* Caddy block does not bound the inbound body (R-526)"
        )


class TestAsgiBodyCap:
    # The MCP StreamableHTTPSessionManager can only .run() once per process,
    # so all cases share one TestClient over one build_app().
    @pytest.fixture(scope="class")
    def client(self):
        from starlette.testclient import TestClient

        from mcp_hosted.serve import build_app

        with TestClient(build_app()) as client:
            yield client

    def test_an_oversized_content_length_is_413(self, client):
        resp = client.post(
            "/mcp",
            content=b"x",
            headers={
                "content-type": "application/json",
                "content-length": str(hosted.MAX_REQUEST_BYTES + 1),
                "host": "127.0.0.1",
            },
        )
        assert resp.status_code == 413

    def test_an_oversized_streamed_body_never_buffers_fully(self, client):
        big = b"x" * (hosted.MAX_REQUEST_BYTES + 65536)
        resp = client.post(
            "/mcp",
            content=big,
            headers={"content-type": "application/json", "host": "127.0.0.1"},
        )
        assert resp.status_code == 413

    def test_a_normal_sized_request_reaches_the_mcp_app(self, client):
        resp = client.post(
            "/mcp",
            content=b'{"jsonrpc": "2.0", "method": "ping", "id": 1}',
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "host": "127.0.0.1",
            },
        )
        assert resp.status_code != 413


class TestUpstreamReadIsStreamed:
    def test_http_get_bounds_allocation_not_just_return(self, monkeypatch):
        """R-550: the bound must apply while reading, via stream=True +
        iter_content, never after requests buffered the whole body."""
        seen: dict = {}

        class FakeResp:
            status_code = 200

            def iter_content(self, chunk_size):
                seen["iterated"] = True
                for _ in range(200):
                    yield b"y" * 65536  # 12.8MB total if never bounded

            def close(self):
                seen["closed"] = True

        def fake_get(url, headers=None, timeout=None, stream=None):
            seen["stream"] = stream
            return FakeResp()

        import requests

        monkeypatch.setattr(requests, "get", fake_get)
        with pytest.raises(ValueError, match="exceeded"):
            hosted._http_get("https://app.radon.run/x", {})
        assert seen.get("stream") is True, "_http_get did not pass stream=True"
        assert seen.get("iterated") is True

    def test_a_small_body_round_trips(self, monkeypatch):
        class FakeResp:
            status_code = 200

            def iter_content(self, chunk_size):
                yield b'{"ok": true}'

            def close(self):
                pass

        import requests

        monkeypatch.setattr(
            requests, "get", lambda url, headers=None, timeout=None, stream=None: FakeResp()
        )
        result = hosted._http_get("https://app.radon.run/x", {})
        assert result.status == 200
        assert result.json() == {"ok": True}


class TestJwksBounding:
    """R-551: port the FastAPI JWKS bounding controls to the MCP mirror."""

    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        monkeypatch.setattr(mcp_auth, "_jwks_negative", type(mcp_auth._jwks_negative)())
        monkeypatch.setattr(
            mcp_auth, "_jwks_gate",
            threading.BoundedSemaphore(mcp_auth.MAX_JWKS_INFLIGHT),
        )

    def _token_with_kid(self, kid: str) -> str:
        # _signing_key_for only reads the header, so an unsigned shell works.
        import jwt as pyjwt

        return pyjwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"kid": kid})

    def test_a_failed_kid_is_negative_cached(self, monkeypatch):
        calls = []

        class FailingClient:
            def get_signing_keys(self):
                return []

            def get_signing_key_from_jwt(self, token):
                import jwt as pyjwt

                calls.append(1)
                # A token-level verdict, not an upstream failure: R-606 caches
                # only the former (see TestOutageIsNotASignatureVerdict).
                raise pyjwt.exceptions.InvalidTokenError("kid not found")

        monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: FailingClient())
        token = self._token_with_kid("bad-kid-1")
        with pytest.raises(Exception):
            mcp_auth._signing_key_for(token)
        with pytest.raises(AuthError) as exc:
            mcp_auth._signing_key_for(token)
        assert exc.value.status == 401
        assert len(calls) == 1, "the second lookup for a failed kid hit JWKS again"

    def test_saturated_inflight_is_a_fast_503(self, monkeypatch):
        class SlowClient:
            def get_signing_keys(self):
                return []

            def get_signing_key_from_jwt(self, token):
                raise AssertionError("must not be reached when saturated")

        monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: SlowClient())
        for _ in range(mcp_auth.MAX_JWKS_INFLIGHT):
            assert mcp_auth._jwks_gate.acquire(blocking=False)
        with pytest.raises(AuthError) as exc:
            mcp_auth._signing_key_for(self._token_with_kid("fresh-kid"))
        assert exc.value.status == 503

    def test_random_kid_flood_is_bounded_per_kid(self, monkeypatch):
        calls = []

        class FailingClient:
            def get_signing_keys(self):
                return []

            def get_signing_key_from_jwt(self, token):
                import jwt as pyjwt

                calls.append(1)
                # A token-level verdict, not an upstream failure: R-606 caches
                # only the former (see TestOutageIsNotASignatureVerdict).
                raise pyjwt.exceptions.InvalidTokenError("kid not found")

        monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: FailingClient())
        for i in range(50):
            token = self._token_with_kid(f"kid-{i % 5}")
            with pytest.raises(Exception):
                mcp_auth._signing_key_for(token)
        assert len(calls) <= 5, (
            f"50 requests over 5 kids made {len(calls)} JWKS fetches"
        )

    def test_the_jwks_client_carries_a_timeout(self, monkeypatch):
        seen = {}

        class FakePyJWKClient:
            def __init__(self, url, cache_keys=False, timeout=None, **kw):
                seen["timeout"] = timeout

        import jwt as pyjwt

        monkeypatch.setattr(pyjwt, "PyJWKClient", FakePyJWKClient)
        monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example.test/jwks.json")
        monkeypatch.setattr(mcp_auth, "_jwks_client", None)
        mcp_auth._get_jwks_client()
        assert isinstance(seen.get("timeout"), (int, float)) and seen["timeout"] <= 10


class TestOutageIsNotASignatureVerdict:
    """R-606 (P1): the `except Exception` around the JWKS lookup recorded the
    token's REAL kid in the negative cache for ANY failure, including a
    timeout or a 5xx from Clerk. `_negative_kid_active` then short-circuits
    every later request with a 401 served without an upstream attempt, so
    nothing re-probes and a one-off blip denies every valid token for 30s.

    R-620 (P2): the refetch cooldown was one module-global scalar, so a flood
    of random kids against this internet-facing surface kept it perpetually in
    the future and a genuinely-new kid never triggered a refresh — key
    rotation became a total auth outage."""

    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        monkeypatch.setattr(mcp_auth, "_jwks_negative", type(mcp_auth._jwks_negative)())
        monkeypatch.setattr(
            mcp_auth, "_jwks_refresh_after", type(mcp_auth._jwks_refresh_after)()
        )
        monkeypatch.setattr(
            mcp_auth, "_jwks_gate",
            threading.BoundedSemaphore(mcp_auth.MAX_JWKS_INFLIGHT),
        )

    def _token_with_kid(self, kid: str) -> str:
        import jwt as pyjwt

        return pyjwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"kid": kid})

    def test_a_timeout_is_an_outage_and_the_kid_stays_reprobeable(self, monkeypatch):
        state = {"fail": True}

        class Flaky:
            def get_signing_keys(self):
                if state["fail"]:
                    raise TimeoutError("clerk timed out")
                return [type("K", (), {"key_id": "kid-a"})()]

            def get_signing_key_from_jwt(self, token):
                return type("S", (), {"key": "the-key"})()

        monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: Flaky())
        token = self._token_with_kid("kid-a")

        with pytest.raises(AuthError) as first:
            mcp_auth._signing_key_for(token)
        assert first.value.status != 401, "an upstream outage is not a 401"

        state["fail"] = False
        assert mcp_auth._signing_key_for(token) == "the-key", (
            "the same kid must be re-probeable after a transient failure"
        )

    def test_a_genuine_unknown_kid_is_still_cached(self, monkeypatch):
        calls = []

        class Known:
            def get_signing_keys(self):
                calls.append(1)
                return [type("K", (), {"key_id": "kid-real"})()]

            def get_signing_key_from_jwt(self, token):
                import jwt as pyjwt

                raise pyjwt.exceptions.InvalidTokenError("no such kid")

        monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: Known())
        token = self._token_with_kid("kid-bogus")
        for _ in range(3):
            with pytest.raises(AuthError) as exc:
                mcp_auth._signing_key_for(token)
            assert exc.value.status == 401
        assert len(calls) == 1, f"a cached bogus kid re-hit JWKS: {len(calls)}"

    def test_a_random_kid_flood_does_not_starve_a_new_kid(self, monkeypatch):
        """Key rotation during a flood: kid B is genuinely new and must still
        get its refetch even though kid A just consumed a cooldown."""
        keys = {"set": ["kid-old"]}

        class Rotating:
            def get_signing_keys(self):
                return [type("K", (), {"key_id": k})() for k in keys["set"]]

            def get_signing_key_from_jwt(self, token):
                import jwt as pyjwt

                kid = pyjwt.get_unverified_header(token).get("kid")
                if kid in keys["set"]:
                    return type("S", (), {"key": f"key-{kid}"})()
                raise pyjwt.exceptions.InvalidTokenError("no such kid")

        monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: Rotating())

        for i in range(20):
            with pytest.raises(AuthError):
                mcp_auth._signing_key_for(self._token_with_kid(f"random-{i}"))

        keys["set"] = ["kid-old", "kid-new"]
        assert mcp_auth._signing_key_for(self._token_with_kid("kid-new")) == "key-kid-new"
