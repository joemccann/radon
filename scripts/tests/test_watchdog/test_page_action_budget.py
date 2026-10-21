"""The daily action cap must count agent launches, not live ticket claims.

`GROK_PAGE_MAX_ACTIONS_PER_DAY` bounds how often an `--always-approve`
agent is launched over untrusted page text. `record_attempt_failure`
clears `claimed_at` so the ticket can be RETRIED (intentional, must not
change) — so a budget read keyed on `claimed_at` loses every failed
launch, and fallback launches were never reserved at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import grok_runtime
from grok_page_responder import run_cycle
from watchdog.pages import (
    actions_since,
    enqueue_delivered_page,
    record_action_attempt,
    record_attempt_failure,
)


NOW = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)
DAY_START = datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc)

RUNTIME = grok_runtime.ResolvedRuntime(
    model="grok-4.7",
    reasoning_effort="high",
    cli_version="1.0.41",
    binary_path="grok",
    used_fallback=False,
    warning=None,
    refused=False,
    source="models",
)


def _enqueue() -> str:
    return enqueue_delivered_page(
        service="vcg-scan",
        severity="P1",
        kind="stale",
        message="silent",
        now=NOW,
    )


@pytest.fixture(autouse=True)
def _enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
    monkeypatch.setenv("RADON_GROK_RUNTIME_LOCK", str(tmp_path / "runtime.lock"))
    monkeypatch.setenv("RADON_GROK_LKG_PATH", str(tmp_path / "absent.json"))


class TestFailedLaunchesConsumeBudget:
    def test_nonzero_exit_still_spends_one_action(self, db_conn, tmp_path):
        _enqueue()
        run_cycle(
            tmp_path,
            now=NOW,
            grok_runner=lambda *_a, **_k: SimpleNamespace(
                returncode=1, stdout="", stderr="auth failed"
            ),
            pin_runtime=RUNTIME,
        )
        assert actions_since(since=DAY_START) == 1

    def test_timeout_still_spends_one_action(self, db_conn, tmp_path):
        _enqueue()

        def timeout(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="grok", timeout=1)

        run_cycle(tmp_path, now=NOW, grok_runner=timeout, pin_runtime=RUNTIME)
        assert actions_since(since=DAY_START) == 1

    def test_successful_run_spends_one_action(self, db_conn, tmp_path):
        _enqueue()

        def ok(*_a, **_k):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"text": "RESULT: stand_down | off-hours lag"}),
                stderr="",
            )

        with patch("grok_page_responder._send_followup"):
            run_cycle(tmp_path, now=NOW, grok_runner=ok, pin_runtime=RUNTIME)
        assert actions_since(since=DAY_START) == 1

    def test_fallback_launch_spends_its_own_action(
        self, db_conn, tmp_path, monkeypatch
    ):
        lkg_path = tmp_path / "grok_lkg.json"
        lkg_path.write_text(json.dumps({
            "cli_version": "1.0.3",
            "binary_path": str(tmp_path / "lkg-grok"),
            "model": "grok-4.6",
            "reasoning_effort": "high",
            "promoted_at": "2026-09-01T00:00:00Z",
            "smoke_result": "stand_down: previous",
        }), encoding="utf-8")
        monkeypatch.setenv("RADON_GROK_LKG_PATH", str(lkg_path))
        monkeypatch.setattr(grok_runtime, "lkg_binary_problem", lambda *_a, **_k: None)
        _enqueue()

        calls: list[list[str]] = []

        def runner(cmd, **_kwargs):
            calls.append(list(cmd))
            if cmd[cmd.index("-m") + 1] == "grok-4.7":
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="model grok-4.7 is unavailable"
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"text": "RESULT: stand_down | recovered"}),
                stderr="",
            )

        with patch("grok_page_responder._send_followup"), patch(
            "grok_page_responder._send_pin_warning"
        ):
            run_cycle(tmp_path, now=NOW, grok_runner=runner, pin_runtime=RUNTIME)

        assert len(calls) == 2
        assert actions_since(since=DAY_START) == 2

    def test_three_failed_attempts_exhaust_a_cap_of_three(
        self, db_conn, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("GROK_PAGE_MAX_ACTIONS_PER_DAY", "3")
        _enqueue()
        boom = lambda *_a, **_k: SimpleNamespace(  # noqa: E731
            returncode=1, stdout="", stderr="auth failed"
        )
        launches = 0
        for _ in range(5):
            before = actions_since(since=DAY_START)
            run_cycle(tmp_path, now=NOW, grok_runner=boom, pin_runtime=RUNTIME)
            launches += actions_since(since=DAY_START) - before
        assert launches == 3

    def test_lkg_fallback_cannot_take_a_seventh_launch(self, db_conn, tmp_path, monkeypatch):
        """DS-2026-10-21-18: the cap was checked, then each launch inserted
        unconditionally. With 5 reserved and a cap of 6, the failed launch
        and its last-known-good fallback were both admitted."""
        monkeypatch.setenv("GROK_PAGE_MAX_ACTIONS_PER_DAY", "6")
        page_id = _enqueue()
        for _ in range(5):
            assert record_action_attempt(page_id=page_id, now=NOW, cap=6, since=DAY_START)
        lkg_path = tmp_path / "grok_lkg.json"
        lkg_path.write_text(json.dumps({
            "cli_version": "1.0.3",
            "binary_path": str(tmp_path / "lkg-grok"),
            "model": "grok-4.6",
            "reasoning_effort": "high",
            "promoted_at": "2026-09-01T00:00:00Z",
            "smoke_result": "stand_down: previous",
        }), encoding="utf-8")
        monkeypatch.setenv("RADON_GROK_LKG_PATH", str(lkg_path))
        monkeypatch.setattr(grok_runtime, "lkg_binary_problem", lambda *_a, **_k: None)
        calls: list[list[str]] = []

        def runner(cmd, **_kwargs):
            calls.append(list(cmd))
            return SimpleNamespace(
                returncode=1, stdout="", stderr="model grok-4.7 is unavailable"
            )

        with patch("grok_page_responder._send_pin_warning"):
            run_cycle(tmp_path, now=NOW, grok_runner=runner, pin_runtime=RUNTIME)
        assert len(calls) == 1
        assert actions_since(since=DAY_START) == 6


class TestRetrySemanticsUnchanged:
    def test_failure_clears_claimed_at_and_leaves_the_ticket_actionable(
        self, db_conn, tmp_path
    ):
        pid = _enqueue()
        run_cycle(
            tmp_path,
            now=NOW,
            grok_runner=lambda *_a, **_k: SimpleNamespace(
                returncode=1, stdout="", stderr="auth failed"
            ),
            pin_runtime=RUNTIME,
        )
        row = db_conn.execute(
            "SELECT status, claimed_at, claim_token, attempts "
            "FROM watchdog_pages WHERE page_id=?",
            (pid,),
        ).fetchone()
        assert row == ("pending", None, None, 1)

    def test_third_failure_still_skips_the_ticket(self, db_conn, tmp_path):
        pid = _enqueue()
        record_attempt_failure(pid, now=NOW, error="x")
        record_attempt_failure(pid, now=NOW, error="x")
        assert record_attempt_failure(pid, now=NOW, error="x") == "skipped"
        assert db_conn.execute(
            "SELECT status, claimed_at, attempts FROM watchdog_pages WHERE page_id=?",
            (pid,),
        ).fetchone() == ("skipped", None, 3)
