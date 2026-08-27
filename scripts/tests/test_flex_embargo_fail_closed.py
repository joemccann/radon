"""The Flex 1025 embargo must fail CLOSED and have an escape hatch.

R-212: `_health_rows` catches every exception from `hrana_query` and returns
`[]`, so "sidecar absent" — a deploy that wiped the gitignored `data/` tree, or
`_arm_sidecar` returning False on a read-only `data/` — PLUS one transient
Turso read error re-opened the guard entirely. Every Flex caller then issues a
SendRequest against a token under a live IBKR lockout, and each such request
EXTENDS the lockout at IBKR's end. Nothing catches it: the failure is a
`log.warning` on a path whose successful outcome is also silence.

R-213: `clear()` only unlinks the sidecar and has no caller outside
`active_until`'s own lapse branch, so the only manual escape is deleting the
sidecar — which the very next `active_until()` silently undoes by rehydrating
and RE-ARMING from the still-`error` health row. And
`_deadline_from_health_row` synthesises `last_attempt + 7d` from
`last_attempt_finished_at` while `_is_1025_error` accepts any row whose message
merely CONTAINS "1025"; the cash-flow handler writes exactly that shape with no
`code` key and re-stamps `last_attempt_finished_at` on every failed run. A
health row can therefore mint a brand-new 7-day deadline with no IBKR 1025
event behind it, and `record_lockout`'s extend-only guard reads that synthetic
deadline as the incumbent — defeating the R-100 fix from underneath.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import flex_embargo as fe  # noqa: E402


T0 = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(fe, "SIDECAR", tmp_path / "flex_token_embargo.json")
    monkeypatch.setattr(fe, "_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(fe, "_heartbeat_ok", lambda *a, **k: None)
    yield


class TestFailsClosedOnAnUnreadableStore:
    @pytest.fixture(autouse=True)
    def _store_is_configured(self, monkeypatch):
        """Production shape: Turso IS configured, so a durable record could
        exist to be lost. Without credentials the sidecar is the only record
        and its absence is genuine information (see the last case below)."""
        monkeypatch.setattr(fe, "_durable_store_available", lambda: True)

    def test_an_unreadable_health_store_with_no_sidecar_blocks(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("turso read failed")

        monkeypatch.setattr(fe, "_query_health_rows", boom)
        assert fe.is_blocked(now=T0) is True, (
            "sidecar absent + one transient read error re-opened the guard; "
            "every Flex caller would SendRequest into a live IBKR lockout"
        )

    def test_it_raises_rather_than_letting_a_caller_through(self, monkeypatch):
        monkeypatch.setattr(
            fe, "_query_health_rows",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("turso down")),
        )
        with pytest.raises(fe.FlexTokenLocked):
            fe.raise_if_blocked(now=T0)

    def test_a_readable_store_with_no_lockout_row_does_not_block(self, monkeypatch):
        monkeypatch.setattr(fe, "_query_health_rows", lambda *a, **k: [])
        assert fe.is_blocked(now=T0) is False

    def test_a_live_sidecar_does_not_need_the_store_at_all(self, monkeypatch):
        fe.record_lockout("1025", now=T0)
        monkeypatch.setattr(
            fe, "_query_health_rows",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not query")),
        )
        assert fe.is_blocked(now=T0 + timedelta(days=1)) is True


class TestOnlyARealLockoutMintsADeadline:
    def _row(self, message, *, code=None, last_attempt=T0):
        import json
        error = {"message": message, "next_attempt_at": None}
        if code is not None:
            error["code"] = code
        return ("error", last_attempt.isoformat(), json.dumps(error))

    def test_a_cash_flow_row_merely_mentioning_1025_mints_nothing(self):
        row = self._row("HTTP 500 from flex gateway (ref 1025488)")
        assert fe._deadline_from_health_row(row) is None, (
            "a substring match minted a brand-new 7-day deadline"
        )

    def test_a_row_with_the_real_lockout_code_still_mints(self):
        row = self._row("Too many failed attempts", code="1025")
        assert fe._deadline_from_health_row(row) is not None

    def test_the_canonical_1025_message_still_mints_without_a_code(self):
        # IBKR's own wording is evidence even when the writer omitted `code`.
        row = self._row("Error 1025: Too many failed attempts, please try again later")
        assert fe._deadline_from_health_row(row) is not None


class TestClearIsARealEscapeHatch:
    def test_clear_survives_the_next_active_until_call(self, monkeypatch):
        import json
        row = ("error", T0.isoformat(), json.dumps({"code": "1025", "message": "1025"}))
        monkeypatch.setattr(fe, "_query_health_rows", lambda service: [row])

        assert fe.is_blocked(now=T0 + timedelta(days=1)) is True
        fe.clear()
        assert fe.is_blocked(now=T0 + timedelta(days=1)) is False, (
            "the escape hatch was undone by the next rehydrate + re-arm"
        )

    def test_a_new_lockout_after_a_clear_still_arms(self, monkeypatch):
        monkeypatch.setattr(fe, "_query_health_rows", lambda service: [])
        fe.record_lockout("1025", now=T0)
        fe.clear()
        assert fe.is_blocked(now=T0 + timedelta(days=1)) is False
        fe.record_lockout("1025", now=T0 + timedelta(days=1))
        assert fe.is_blocked(now=T0 + timedelta(days=2)) is True


class TestClearHasACaller:
    def test_the_cli_clears_a_live_embargo(self, monkeypatch, capsys):
        monkeypatch.setattr(fe, "_query_health_rows", lambda service: [])
        fe.record_lockout("1025", now=T0)
        assert fe.is_blocked(now=T0 + timedelta(days=1)) is True

        assert fe.main(["clear"]) == 0
        assert "cleared flex embargo" in capsys.readouterr().out
        assert fe.is_blocked(now=T0 + timedelta(days=1)) is False

    def test_the_cli_reports_status(self, monkeypatch, capsys):
        monkeypatch.setattr(fe, "_query_health_rows", lambda service: [])
        assert fe.main(["status"]) == 0
        assert "none" in capsys.readouterr().out


class TestTheDurableStoreSwitchItself:
    """`_durable_store_available` is the SOLE switch between fail-closed and
    fail-open, and every other test in this file monkeypatches it away, so the
    real body had zero assertions on it.

    Env only. Nothing here opens a socket, and nothing here may ever issue a
    Flex SendRequest: each one EXTENDS a live IBKR 1025 lockout.
    """

    def test_configured_credentials_report_a_durable_store(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-test.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
        assert fe._durable_store_available() is True

    def test_absent_credentials_report_no_durable_store(self, monkeypatch):
        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
        assert fe._durable_store_available() is False

    def test_a_credential_read_failure_against_a_configured_url_fails_closed(
        self, monkeypatch
    ):
        """R-212: a transient failure to LOAD credentials is not evidence that
        no durable record exists. Reading it as "unconfigured" sends every Flex
        caller into a live 1025 lockout."""
        import db.hrana_http as hrana

        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-test.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
        monkeypatch.setattr(
            hrana, "read_env",
            lambda: (_ for _ in ()).throw(RuntimeError("env not loaded yet")),
        )
        assert fe._durable_store_available() is True

    def test_a_read_failure_with_no_configured_url_still_fails_open(
        self, monkeypatch
    ):
        import db.hrana_http as hrana

        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        monkeypatch.setattr(
            hrana, "read_env",
            lambda: (_ for _ in ()).throw(RuntimeError("env not loaded yet")),
        )
        assert fe._durable_store_available() is False

    def test_the_guard_has_no_pytest_branch(self):
        """A `PYTEST_CURRENT_TEST` branch in production code guarantees the
        natural path can never run under pytest."""
        source = (
            Path(fe.__file__).read_text(encoding="utf-8")
        )
        assert "PYTEST_CURRENT_TEST" not in source, (
            "the fail-closed switch short-circuits under pytest, so no test "
            "can reach the code production runs"
        )


def test_an_unconfigured_store_does_not_embargo_flex(monkeypatch):
    """Fail-closed applies to a CONFIGURED store that failed to answer.

    On a host with no Turso credentials there was never a durable record to
    lose, so blocking there would embargo Flex for a misconfiguration.
    """
    monkeypatch.setattr(fe, "_durable_store_available", lambda: False)
    monkeypatch.setattr(
        fe, "_query_health_rows",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("not configured")),
    )
    assert fe.is_blocked(now=T0) is False
