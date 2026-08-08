"""Pins for the portfolio-snapshot DELETE retry transport classifier.

Covers ``scripts/db/writer.py``'s ``_is_delete_transport_error`` (the
string-marker classifier that decides whether a ``delete_portfolio_snapshots_
before`` failure is a transport blip worth retrying) and ``_hrana_with_retry``
(the retry wrapper built on top of it). Mirrors the contract shape of the JS
twin, ``web/tests/db-writer-bounds.test.ts`` — in particular the "does NOT
retry SQL/constraint errors" case (~line 79) that this file's (c) pins.

Audit note (T-043): every test below is a PIN of current behavior, not a
correctness claim. See NOTES.md for which cases prove what and two surprises
found while tracing the source (a loose-marker false-positive class the task
asked to pin rather than fix, and a bonus finding that ``_hrana_with_retry``
retries ANY ``HranaHttpError`` regardless of its message).

Mocks only — no production Turso (feedback_test_pollution_to_production).
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from urllib.error import URLError

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import db.writer as writer_mod  # noqa: E402
from db.hrana_http import HranaHttpError  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """_hrana_with_retry sleeps 0.5s * attempt between retries (real time.sleep,
    module imported locally inside the function). Neutralize so the retry-path
    tests below run instantly; classification-only tests never call sleep, so
    this is a harmless no-op for them.
    """
    import time as time_mod

    monkeypatch.setattr(time_mod, "sleep", lambda *_args, **_kwargs: None)


# ── (a) _is_delete_transport_error: transport-shaped failures ──────────────

class TestIsDeleteTransportErrorTransportCases:
    """Marker matches the DELETE retry loop must catch and retry."""

    def test_stream_not_found(self):
        assert writer_mod._is_delete_transport_error(
            RuntimeError("stream not found")
        )

    def test_socket_timeout_text(self):
        # The stdlib's actual message for a blocking-call timeout is "timed
        # out" — one of the two literal marker phrases (not just a substring
        # of "timeout"). socket.timeout is TimeoutError as of Python 3.10.
        assert writer_mod._is_delete_transport_error(socket.timeout("timed out"))

    def test_urllib_urlerror_text(self):
        # hrana_execute/hrana_query (scripts/db/hrana_http.py) wrap any
        # non-HranaHttpError exception as f"{type(exc).__name__}: {exc}"
        # before it ever reaches this classifier. That wrapped type-name
        # PREFIX is what actually supplies the "urlerror" marker match — see
        # test_bare_urlerror_object_does_not_match_without_hrana_wrapping
        # below for the contrast. This test reproduces the wrapped shape a
        # real URLError takes by the time _hrana_with_retry ever sees it.
        original = URLError("Name or service not known")
        wrapped = HranaHttpError(f"{type(original).__name__}: {original}")
        assert writer_mod._is_delete_transport_error(wrapped)

    def test_bare_urlerror_object_does_not_match_without_hrana_wrapping(self):
        # Contrast case: URLError's own str() is "<urlopen error ...>", which
        # does NOT contain "urlerror". A caller that classifies a raw URLError
        # directly (bypassing hrana_http's wrapping) would get False here.
        # Documents the boundary so nobody "simplifies" the test above into
        # this form and starts silently testing something that is False.
        assert not writer_mod._is_delete_transport_error(
            URLError("Name or service not known")
        )

    def test_upstream_forward_failed_502(self):
        # There is no dedicated "502"/"upstream" marker in
        # _DELETE_TRANSPORT_MARKERS. This passes only because "stream" is a
        # substring of "upstream" — a coincidental match, not a deliberate
        # one. Text is the literal production message quoted in
        # scripts/CLAUDE.md's Turso Hrana I/O Bounding section.
        assert writer_mod._is_delete_transport_error(
            HranaHttpError("upstream forward failed (502)")
        )


# ── (b) _is_delete_transport_error: SQL/constraint failures ────────────────

class TestIsDeleteTransportErrorSqlCases:
    """SQL/constraint errors must NOT be treated as retryable transport —
    the direct Python analogue of the JS twin's "does NOT retry SQL/
    constraint errors" case.
    """

    def test_unique_constraint_failed(self):
        assert not writer_mod._is_delete_transport_error(
            HranaHttpError("UNIQUE constraint failed: cash_flows.id")
        )

    def test_no_such_table(self):
        assert not writer_mod._is_delete_transport_error(
            HranaHttpError("no such table: portfolio_snapshots")
        )

    def test_foreign_key_constraint_failed(self):
        assert not writer_mod._is_delete_transport_error(
            HranaHttpError("FOREIGN KEY constraint failed")
        )


# ── CAREFUL: loose 'closed' / 'connection' markers ──────────────────────────

class TestIsDeleteTransportErrorLooseMarkersPinnedNotFixed:
    """'closed' and 'connection' are bare single-word markers in
    _DELETE_TRANSPORT_MARKERS. They exist to catch real transport phrasing
    ("stream closed", "connection reset") but as bare substrings they ALSO
    match unrelated messages that merely contain those words — including a
    plausible driver-state message like "database is closed" that has
    nothing to do with the network. This is the audit's explicit CAREFUL
    case: these two tests PIN the current (loose) behavior. They are not a
    claim it's correct, and this file makes no attempt to tighten it — a
    future narrowing of the marker list should be a deliberate, reviewed
    diff, not something discovered by an unrelated test flipping color.
    """

    def test_database_is_closed_text_is_treated_as_transport(self):
        assert writer_mod._is_delete_transport_error(
            RuntimeError("database is closed")
        )

    def test_message_merely_mentioning_connection_is_treated_as_transport(self):
        assert writer_mod._is_delete_transport_error(
            RuntimeError("invalid connection string in config")
        )


# ── (c) _hrana_with_retry: non-transport error → exactly one call ──────────

class TestHranaWithRetryNonTransportError:
    def test_calls_fn_once_and_reraises_for_non_transport_error(self):
        calls: list[int] = []

        def fn():
            calls.append(1)
            # Plain (non-HranaHttpError) exception whose message matches no
            # _DELETE_TRANSPORT_MARKERS — the direct analogue of the JS
            # twin's sqlError() in db-writer-bounds.test.ts. Deliberately NOT
            # a HranaHttpError: see the bonus pin below for why that matters.
            raise RuntimeError("UNIQUE constraint failed: cash_flows.id")

        with pytest.raises(RuntimeError, match="UNIQUE constraint failed"):
            writer_mod._hrana_with_retry(fn)

        assert len(calls) == 1


# ── (d) _hrana_with_retry: transport error → retries once, then succeeds ───

class TestHranaWithRetryTransportError:
    def test_retries_once_then_succeeds_for_transport_error(self):
        calls: list[int] = []

        def fn():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("stream not found")
            return "ok"

        result = writer_mod._hrana_with_retry(fn)

        assert result == "ok"
        assert len(calls) == 2  # original attempt + 1 retry


# ── Bonus pin: HranaHttpError retries regardless of message ────────────────

class TestHranaWithRetryHranaHttpErrorBypassesMarkerCheck:
    """Not one of the T-043 (a)-(d) cases — a finding surfaced while tracing
    _hrana_with_retry's condition for this audit, pinned here because prose
    alone in NOTES.md would be easy to lose track of.

    The retry gate is:

        if (not isinstance(exc, HranaHttpError) and not _is_delete_transport_error(exc))
           or attempt + 1 >= attempts:
            raise

    ``isinstance(exc, HranaHttpError)`` short-circuits the marker check via
    De Morgan: ANY HranaHttpError retries (until attempts are exhausted),
    regardless of what _is_delete_transport_error would say about its
    message. Since hrana_execute/hrana_query (scripts/db/hrana_http.py)
    ALWAYS wrap failures — including genuine SQL errors — as HranaHttpError,
    _is_delete_transport_error is effectively dead for the real callers of
    _hrana_with_retry inside delete_portfolio_snapshots_before: production
    retries ANY hrana failure once, not just transport-shaped ones. This is
    a scope/audit finding, not a bug this task fixes.
    """

    def test_hrana_http_error_retries_even_with_sql_shaped_message(self):
        calls: list[int] = []

        def fn():
            calls.append(1)
            if len(calls) == 1:
                raise HranaHttpError("UNIQUE constraint failed: cash_flows.id")
            return "ok"

        result = writer_mod._hrana_with_retry(fn)

        assert result == "ok"
        assert len(calls) == 2
