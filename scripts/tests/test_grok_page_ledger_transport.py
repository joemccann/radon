"""Follow-up to the ledger read-timeout case: classify Turso transport
blips by exception type, recover a lost complete_page / claim_page, and
emit a non-paging signal.

The live poller holds /var/lib/radon/grok-runtime/grok-runtime.lock for
the whole grok subprocess. Every run_cycle fixture in this file points
RADON_GROK_RUNTIME_LOCK at a private tmp path so the test cannot flock
the VPS lock.
"""

from __future__ import annotations

import http.client
import io
import json
import ssl
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_page_responder as responder  # noqa: E402
from db.hrana_http import (  # noqa: E402
    HranaHttpError,
    HttpStatusError,
    is_transport_error,
)
from watchdog import pages as pages_mod  # noqa: E402
from watchdog import check as check_mod  # noqa: E402

NOW = datetime(2026, 10, 17, 17, 0, tzinfo=timezone.utc)


def _wrap(cause: BaseException) -> HranaHttpError:
    exc = HranaHttpError(f"{type(cause).__name__}: {cause}")
    exc.__cause__ = cause
    return exc


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://turso.example/v2/pipeline",
        status,
        "synthetic",
        None,
        io.BytesIO(),
    )


def _page() -> dict:
    return {
        "page_id": "eaaaff8194a0fb8086edc8f55bb438ed",
        "service": "radon-grok-page-responder.service",
        "severity": "P1",
        "kind": "unit",
        "message_excerpt": (
            "<untrusted-excerpt>systemd unit failed "
            "(Result=exit-code, NRestarts=0)</untrusted-excerpt>"
        ),
        "paged_at": "2026-10-17T06:55:00Z",
        "status": "pending",
        "claimed_at": None,
        "attempts": 0,
    }


class _Proc:
    returncode = 0
    stdout = "RESULT: stand_down | ok\n"
    stderr = ""


@pytest.fixture
def cycle_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
    monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "0")
    monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "0")
    monkeypatch.setenv("GROK_PAGE_SYNC_REMOTE", "0")
    monkeypatch.setenv("RADON_GROK_RUNTIME_LOCK", str(tmp_path / "runtime.lock"))
    heartbeats: list[dict] = []
    followups: list[dict] = []
    invocations: list = []
    monkeypatch.setattr(
        responder,
        "record_cycle_health",
        lambda state, **kwargs: heartbeats.append({"state": state, **kwargs}),
    )
    monkeypatch.setattr(
        responder, "_send_followup", lambda **kwargs: followups.append(kwargs)
    )
    monkeypatch.setattr(pages_mod, "actions_since", lambda **_k: 0)
    monkeypatch.setattr(pages_mod, "list_actionable_pages", lambda **_k: [_page()])
    monkeypatch.setattr(pages_mod, "claim_page", lambda *_a, **_k: True)
    monkeypatch.setattr(pages_mod, "get_page", lambda *_a, **_k: None)

    def runner(*_a, **_k):
        invocations.append(_a)
        return _Proc()

    return {
        "root": tmp_path,
        "heartbeats": heartbeats,
        "followups": followups,
        "invocations": invocations,
        "runner": runner,
        "lock": tmp_path / "data" / "cache" / "grok_pages" / responder.LOCK_NAME,
    }


class TestLedgerTransportClassifier:
    @pytest.mark.parametrize(
        "cause",
        [
            TimeoutError("The read operation timed out"),
            http.client.RemoteDisconnected(
                "Remote end closed connection without response"
            ),
            ConnectionResetError("Connection reset by peer"),
            BrokenPipeError("Broken pipe"),
            ssl.SSLEOFError(ssl.SSL_ERROR_EOF, "EOF occurred in violation of protocol"),
            urllib.error.URLError("timed out"),
            HttpStatusError(502),
            HttpStatusError(503),
            HttpStatusError(504),
            _http_error(502),
            _http_error(503),
            _http_error(504),
        ],
    )
    def test_wire_failures_are_transport(self, cause):
        assert is_transport_error(cause) is True
        assert is_transport_error(_wrap(cause)) is True

    @pytest.mark.parametrize(
        "exc",
        [
            HranaHttpError("no such table: watchdog_pages"),
            HranaHttpError("TURSO_DB_URL / TURSO_AUTH_TOKEN not configured"),
            HranaHttpError(
                "RemoteDisconnected: Remote end closed connection without response"
            ),
            HttpStatusError(401),
            HttpStatusError(404),
            HttpStatusError(429),
            _http_error(401),
            _http_error(404),
            FileNotFoundError("missing"),
        ],
    )
    def test_statement_and_client_errors_are_not_transport(self, exc):
        assert is_transport_error(exc) is False
        if not isinstance(exc, HranaHttpError):
            assert is_transport_error(_wrap(exc)) is False


class TestLostCompletePage:
    def test_retry_on_fresh_socket_sends_the_followup(
        self, cycle_env, monkeypatch, capsys
    ):
        calls = {"n": 0}

        def complete(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _wrap(
                    http.client.RemoteDisconnected(
                        "Remote end closed connection without response"
                    )
                )

        monkeypatch.setattr(pages_mod, "complete_page", complete)

        rc = responder.run_cycle(
            cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
        )

        assert rc == 0
        assert calls["n"] == 2
        assert len(cycle_env["invocations"]) == 1
        assert len(cycle_env["followups"]) == 1
        assert cycle_env["heartbeats"][0]["state"] == "ok"
        assert cycle_env["heartbeats"][0]["error"]["class"] == "ledger_transport"
        assert cycle_env["heartbeats"][0]["error"]["recovered"] is True
        assert not cycle_env["lock"].exists()
        out = capsys.readouterr().out
        payload = [
            json.loads(line)
            for line in out.splitlines()
            if "grok_page_ledger_transport" in line
        ]
        assert payload and payload[0]["recovered"] is True
        assert payload[0]["op"] == "complete_page"

    def test_readback_of_done_row_sends_the_followup(
        self, cycle_env, monkeypatch
    ):
        def boom(*_a, **_k):
            raise _wrap(ConnectionResetError("Connection reset by peer"))

        monkeypatch.setattr(pages_mod, "complete_page", boom)
        monkeypatch.setattr(
            pages_mod,
            "get_page",
            lambda *_a, **_k: {
                **_page(),
                "status": "done",
                "result": "stand_down: ok",
            },
        )

        rc = responder.run_cycle(
            cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
        )

        assert rc == 0
        assert len(cycle_env["followups"]) == 1
        assert cycle_env["heartbeats"][0]["state"] == "ok"
        assert cycle_env["heartbeats"][0]["error"]["recovered"] is True

    def test_unknown_outcome_skips_followup_and_ok_heartbeat(
        self, cycle_env, monkeypatch, capsys
    ):
        def boom(*_a, **_k):
            raise _wrap(BrokenPipeError("Broken pipe"))

        monkeypatch.setattr(pages_mod, "complete_page", boom)
        monkeypatch.setattr(
            pages_mod, "get_page", lambda *_a, **_k: {**_page(), "status": "claimed"}
        )

        rc = responder.run_cycle(
            cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
        )

        assert rc == 0
        assert cycle_env["followups"] == []
        assert cycle_env["heartbeats"] == []
        assert not cycle_env["lock"].exists()
        err = capsys.readouterr().err
        assert "grok page ledger transport" in err
        assert "grok page ledger non-fatal" in err

    def test_statement_error_still_fails_the_oneshot(self, cycle_env, monkeypatch):
        def boom(*_a, **_k):
            raise HranaHttpError("no such table: watchdog_pages")

        monkeypatch.setattr(pages_mod, "complete_page", boom)

        with pytest.raises(HranaHttpError, match="no such table"):
            responder.run_cycle(
                cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
            )

        assert cycle_env["followups"] == []
        assert cycle_env["heartbeats"] == []
        assert not cycle_env["lock"].exists()


class TestLostClaimPage:
    def test_matching_token_after_transport_owns_the_row(self, monkeypatch):
        token = "tok-ours"
        page_id = _page()["page_id"]

        def execute(*_a, **_k):
            raise _wrap(ConnectionResetError("Connection reset by peer"))

        def query(*_a, **_k):
            return [("claimed", token)]

        monkeypatch.setattr("db.hrana_http.hrana_execute", execute)
        monkeypatch.setattr("db.hrana_http.hrana_query", query)

        assert pages_mod.claim_page(page_id, now=NOW, claim_token=token) is True

    def test_pending_after_transport_retries_the_update(self, monkeypatch):
        token = "tok-ours"
        page_id = _page()["page_id"]
        executes = {"n": 0}

        def execute(*_a, **_k):
            executes["n"] += 1
            if executes["n"] == 1:
                raise _wrap(HttpStatusError(503))

        def query(*_a, **_k):
            if executes["n"] == 1:
                return [("pending", None)]
            return [("claimed", token)]

        monkeypatch.setattr("db.hrana_http.hrana_execute", execute)
        monkeypatch.setattr("db.hrana_http.hrana_query", query)

        assert pages_mod.claim_page(page_id, now=NOW, claim_token=token) is True
        assert executes["n"] == 2

    def test_foreign_token_is_lost_claim(self, monkeypatch):
        def execute(*_a, **_k):
            raise _wrap(BrokenPipeError("Broken pipe"))

        def query(*_a, **_k):
            return [("claimed", "tok-other")]

        monkeypatch.setattr("db.hrana_http.hrana_execute", execute)
        monkeypatch.setattr("db.hrana_http.hrana_query", query)

        assert (
            pages_mod.claim_page(_page()["page_id"], now=NOW, claim_token="tok-ours")
            is False
        )

    def test_double_fault_does_not_launch_grok(
        self, cycle_env, monkeypatch, capsys
    ):
        def boom(*_a, **_k):
            raise _wrap(TimeoutError("The read operation timed out"))

        monkeypatch.setattr(pages_mod, "claim_page", boom)

        rc = responder.run_cycle(
            cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
        )

        assert rc == 0
        assert cycle_env["invocations"] == []
        assert cycle_env["heartbeats"] == []
        assert cycle_env["followups"] == []
        assert not cycle_env["lock"].exists()
        err = capsys.readouterr().err
        assert "grok page ledger non-fatal" in err


class TestLedgerTransportSignal:
    def test_ok_last_error_does_not_fire_the_error_bucket(self, monkeypatch):
        monkeypatch.setattr(check_mod.cooldown_mod, "record_success", lambda **_k: None)
        outcome = check_mod._check_error(
            service="grok-page-responder",
            health={
                "state": "ok",
                "last_error": json.dumps(
                    {
                        "class": "ledger_transport",
                        "op": "complete_page",
                        "recovered": True,
                    }
                ),
            },
            now=NOW,
            market_state="closed",
        )
        assert outcome.status == "healthy"
        assert outcome.fired is False
        assert outcome.severity is None

    def test_http_502_from_hrana_is_typed(self, monkeypatch):
        import db.hrana_http as hrana

        monkeypatch.setattr(hrana, "read_env", lambda: ("libsql://fake.invalid", "t"))
        monkeypatch.setattr(hrana, "_refuse_pytest_pollution", lambda: None)
        monkeypatch.setattr(
            hrana,
            "_post_pipeline",
            lambda *_a, **_k: (_ for _ in ()).throw(HttpStatusError(502)),
        )
        with pytest.raises(HranaHttpError) as caught:
            hrana.hrana_execute("SELECT 1")
        assert isinstance(caught.value.__cause__, HttpStatusError)
        assert caught.value.__cause__.status == 502
        assert is_transport_error(caught.value) is True
