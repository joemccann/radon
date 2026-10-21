"""Completed work must not relaunch through an alias of the same Grok binary."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import grok_page_responder as responder
import grok_runtime


@pytest.fixture
def runtimes(tmp_path):
    binary = tmp_path / "grok-1.0.62"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    alias = tmp_path / "grok"
    alias.symlink_to(binary)
    runtime = grok_runtime.ResolvedRuntime(
        model="grok-4.7", reasoning_effort="high", cli_version="1.0.62",
        binary_path=str(alias), used_fallback=False, warning=None,
        refused=False, source="models",
    )
    lkg = grok_runtime.LkgState(
        model="grok-4.7", reasoning_effort="high", cli_version="1.0.62",
        binary_path=str(binary), promoted_at="2026-10-17T07:44:28Z",
        smoke_result="stand_down: smoke passed",
    )
    return runtime, lkg


@pytest.mark.parametrize("output", [
    "The model margin estimate is unavailable.\nRESULT: stand_down | healthy",
    json.dumps({"text": "RESULT: code_fix | committed", "thought": "model unavailable during diagnosis"}),
    "RESULT: CODE_FIX | committed",
])
def test_completed_result_is_not_a_runtime_failure(output):
    assert not grok_runtime.should_retry_lkg(0, output, "")


def test_valid_result_is_not_overridden_by_unrelated_stderr():
    assert not grok_runtime.should_retry_lkg(
        0, "RESULT: stand_down | verified", "Optional model metadata unavailable"
    )


@pytest.mark.parametrize("output", [
    "RESULT: invented | no valid verdict",
    json.dumps({"text": "No final verdict", "thought": "RESULT: code_fix | draft"}),
])
def test_only_a_valid_assistant_result_suppresses_retry(output):
    assert grok_runtime.should_retry_lkg(0, output, "")


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_cycle_does_not_retry_same_binary_alias(monkeypatch, tmp_path, runtimes, failure, capsys):
    runtime, lkg = runtimes
    monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
    monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "0")
    monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "0")
    monkeypatch.setenv("GROK_PAGE_SYNC_REMOTE", "0")
    monkeypatch.setenv("RADON_GROK_RUNTIME_LOCK", str(tmp_path / "runtime.lock"))
    monkeypatch.setattr(responder, "_load_trusted_lkg", lambda *_: lkg)
    monkeypatch.setattr(responder, "record_cycle_health", lambda *_a, **_k: None)
    monkeypatch.setattr(responder.pages_mod, "list_actionable_pages", lambda **_: [{
        "page_id": "72cdada6f65595cfd32c1885c614ed75", "service": "radon-flow-refresh.service",
        "severity": "P1", "kind": "unit", "message_excerpt": "unit failed",
        "paged_at": "2026-10-21T18:05:00Z", "attempts": 0,
    }])
    monkeypatch.setattr(responder.pages_mod, "actions_since", lambda **_: 0)
    monkeypatch.setattr(responder.pages_mod, "claim_page", lambda *_a, **_k: True)
    attempts, warnings, failures, calls = [], [], [], []
    monkeypatch.setattr(responder.pages_mod, "record_action_attempt", lambda **k: attempts.append(k))
    monkeypatch.setattr(responder.pages_mod, "record_attempt_failure", lambda *a, **k: failures.append(k) or "pending")
    monkeypatch.setattr(responder, "_send_pin_warning", warnings.append)
    def runner(cmd, **kwargs):
        calls.append(cmd)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return SimpleNamespace(returncode=1, stdout="", stderr="provider failed: Bearer private-token-value")
    assert responder.run_cycle(tmp_path, now=datetime.now(timezone.utc), grok_runner=runner, pin_runtime=runtime) == 0
    assert len(calls) == len(attempts) == 1
    assert warnings == []
    assert len(failures) == 1
    diagnostics = capsys.readouterr().err
    assert "grok_runtime_attempt" in diagnostics
    assert ("timeout" if failure == "timeout" else "exit") in diagnostics
    assert "private-token-value" not in diagnostics


@pytest.mark.parametrize("change", ["model", "binary", "effort"])
def test_genuinely_distinct_fallback_is_allowed(tmp_path, runtimes, change):
    runtime, lkg = runtimes
    if change == "model":
        lkg = replace(lkg, model="grok-4.6")
    elif change == "effort":
        lkg = replace(lkg, reasoning_effort="low")
    else:
        replacement = tmp_path / "older-grok"
        replacement.write_text("#!/bin/sh\nexit 1\n")
        lkg = replace(lkg, binary_path=str(replacement))
    assert grok_runtime.is_distinct_fallback(runtime, lkg)


def test_hard_link_is_same_executable(tmp_path, runtimes):
    runtime, lkg = runtimes
    hardlink = tmp_path / "hardlink"
    os.link(lkg.binary_path, hardlink)
    runtime = replace(runtime, binary_path=str(hardlink))
    assert not grok_runtime.is_distinct_fallback(runtime, lkg)


def test_bare_command_resolves_through_path(monkeypatch, tmp_path, runtimes):
    runtime, lkg = runtimes
    monkeypatch.setenv("PATH", str(tmp_path))
    runtime = replace(runtime, binary_path="grok")
    assert not grok_runtime.is_distinct_fallback(runtime, lkg)


def test_missing_live_binary_can_use_installed_fallback(tmp_path, runtimes):
    runtime, lkg = runtimes
    runtime = replace(runtime, binary_path=str(tmp_path / "missing"))
    assert grok_runtime.is_distinct_fallback(runtime, lkg)


@pytest.mark.parametrize("output", ["{truncated", '{"text": null}', '{"stopReason": "error"}'])
def test_broken_json_is_not_a_completed_result(output):
    assert grok_runtime.should_retry_lkg(0, output, "")
