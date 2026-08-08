"""Failure-mode contract for scripts/db/hrana_http.py's bounded Turso
HTTP transport.

Monkeypatches urllib.request.urlopen (the only network seam in this module —
see hrana_http.py's module docstring: stdlib urllib only, no libsql import)
to pin how each failure shape surfaces as a HranaHttpError, and whether that
HranaHttpError is classified as retryable transport by db.writer's
_is_delete_transport_error (T-043's companion classifier pins). Also pins
that the caller-supplied timeout reaches urlopen unchanged.

Audit note (T-053): every test is a PIN of current behavior, not a
correctness claim. See NOTES.md for which cases prove what and the one case
(malformed JSON) that pins a plausibly-surprising non-classification.

Mocks only — no production Turso (feedback_test_pollution_to_production).
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.request
from pathlib import Path
from urllib.error import URLError

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.hrana_http import HranaHttpError, hrana_execute  # noqa: E402
from db.writer import _is_delete_transport_error  # noqa: E402


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """hrana_execute short-circuits to HranaHttpError("... not configured")
    before ever calling urlopen unless TURSO_DB_URL / TURSO_AUTH_TOKEN are
    set, and _refuse_pytest_pollution() raises under pytest unless opted in
    (feedback_test_pollution_to_production) — exactly the documented escape
    hatch for tests that exercise this module directly rather than mocking
    it away. Every test below monkeypatches urlopen, so these are inert fake
    credentials; nothing here reaches a real network.
    """
    monkeypatch.setenv("TURSO_DB_URL", "libsql://example-test.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")


class _FakeHttpResponse:
    """Minimal stand-in for the context-manager object urllib.request.urlopen
    returns. hrana_http.py._post_pipeline does exactly this and nothing more:

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES)

    so only __enter__/__exit__ + read(n) are needed.
    """

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, _amt: int | None = None) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None


def _ok_body() -> bytes:
    """Smallest body _check_statement_ok accepts as success — it only reads
    results[0].type, never the response/result payload (that's
    _execute_result's job, used by hrana_query, not hrana_execute)."""
    return json.dumps({"results": [{"type": "ok"}]}).encode("utf-8")


# ── (a) socket.timeout -> HranaHttpError classified as transport ───────────

class TestSocketTimeout:
    def test_socket_timeout_becomes_transport_classified_hrana_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        def fake_urlopen(req, timeout=None):
            raise socket.timeout("timed out")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        with pytest.raises(HranaHttpError) as exc_info:
            hrana_execute("SELECT 1")

        assert _is_delete_transport_error(exc_info.value)
        # Pin the wrapped shape too, not just the boolean outcome — a
        # refactor of the "{type(exc).__name__}: {exc}" wrapping could keep
        # matching a marker by coincidence while breaking the contract.
        assert str(exc_info.value).lower().startswith("timeouterror:")


# ── (b) URLError -> HranaHttpError classified as transport ─────────────────

class TestUrlError:
    def test_urlerror_becomes_transport_classified_hrana_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        def fake_urlopen(req, timeout=None):
            raise URLError("Name or service not known")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        with pytest.raises(HranaHttpError) as exc_info:
            hrana_execute("SELECT 1")

        assert _is_delete_transport_error(exc_info.value)
        # The "urlerror" marker only fires because hrana_execute's generic
        # `except Exception` wraps as f"{type(exc).__name__}: {exc}" — a bare
        # URLError's own str() is "<urlopen error ...>" and does NOT contain
        # "urlerror" (companion contrast pin: T-043's
        # test_bare_urlerror_object_does_not_match_without_hrana_wrapping).
        assert str(exc_info.value).lower().startswith("urlerror:")


# ── (c) HTTP 200 statement error -> HranaHttpError, NOT transport ──────────

class TestStatementError:
    def test_http_200_statement_error_is_not_classified_as_transport(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        body = json.dumps(
            {
                "results": [
                    {
                        "type": "error",
                        "error": {"message": "UNIQUE constraint failed: cash_flows.id"},
                    }
                ]
            }
        ).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            return _FakeHttpResponse(body)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        with pytest.raises(HranaHttpError) as exc_info:
            hrana_execute("DELETE FROM cash_flows WHERE id = ?", ("x",))

        # _check_statement_ok raises HranaHttpError directly (already the
        # right type), so hrana_execute's `except HranaHttpError: raise`
        # re-raises it UNCHANGED — no type-name prefix, unlike the transport
        # cases above. The message is the bare statement error text.
        assert str(exc_info.value) == "UNIQUE constraint failed: cash_flows.id"
        assert not _is_delete_transport_error(exc_info.value)


# ── (d) malformed JSON body -> HranaHttpError (pin, not a classification claim) ──

class TestMalformedJsonBody:
    def test_malformed_json_body_raises_hrana_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        def fake_urlopen(req, timeout=None):
            return _FakeHttpResponse(b"not json{{{")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        with pytest.raises(HranaHttpError) as exc_info:
            hrana_execute("SELECT 1")

        # PIN CURRENT BEHAVIOR — not a claim this is the "right" outcome.
        # json.loads raises json.JSONDecodeError; hrana_execute's generic
        # `except Exception` wraps it as "JSONDecodeError: Expecting value:
        # line 1 column 1 (char 0)". None of _DELETE_TRANSPORT_MARKERS match
        # that text, so a malformed/truncated response body is NOT retried
        # by _hrana_with_retry even though it is arguably transport-adjacent
        # (a corrupted body is closer to "the network lied to us" than to a
        # SQL error). If this assertion ever flips to True, the wrapping
        # format changed — worth a deliberate look, not a shrug.
        assert "jsondecodeerror" in str(exc_info.value).lower()
        assert not _is_delete_transport_error(exc_info.value)


# ── (e) explicit timeout kwarg reaches urlopen unchanged ───────────────────

class TestTimeoutPropagation:
    def test_explicit_timeout_kwarg_reaches_urlopen(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None):
            captured["timeout"] = timeout
            return _FakeHttpResponse(_ok_body())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        hrana_execute("SELECT 1", timeout=9.5)

        assert captured["timeout"] == 9.5

    def test_default_timeout_is_hrana_timeout_s_when_not_overridden(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from db.hrana_http import HRANA_TIMEOUT_S

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None):
            captured["timeout"] = timeout
            return _FakeHttpResponse(_ok_body())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        hrana_execute("SELECT 1")

        assert captured["timeout"] == HRANA_TIMEOUT_S
