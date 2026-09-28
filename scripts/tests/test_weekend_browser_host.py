"""Scheduled providers never receive a shared host Playwright server.

The host browser (~/.radon/agent-cli/browser-host) served only the testing and
reliability wrappers, which moved to scripts/runner/run_loop.sh. What stays
pinned here: the remaining security wrappers strip any inherited endpoint from
every provider and never start a host server themselves.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_rel137_weekend_wrapper_survivability import BASH

REPO = Path(__file__).resolve().parents[2]
WRAPPERS = ["security_nightly", "security_deepsec_nightly"]
ENDPOINT = "ws://127.0.0.1:4711/tok"


@pytest.mark.parametrize("provider", ["claude", "codex", "grok", "nvidia", "cerebras"])
@pytest.mark.parametrize("name", WRAPPERS)
def test_every_provider_discards_inherited_host_endpoint(name, provider, tmp_path):
    source = (REPO / "scripts" / f"{name}.sh").read_text()
    start = source.index("launch_round() {")
    end = source.index("\n  # A bare rung", start)
    # Execute the actual environment prologue with a poisoned inherited endpoint.
    script = source[start:end] + '\n  env\n}\nlaunch_round 60\n'
    env = {**os.environ, "RUNG_PROVIDER": provider, "PW_TEST_CONNECT_WS_ENDPOINT": ENDPOINT,
           "NIGHTLY_PR_GUARD_DIR": str(tmp_path), "PORTABLE_PROMPT_DIR": str(tmp_path),
           "LOOP_SKILL": "unused", "PHASE": "audit"}
    proc = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert "PW_TEST_CONNECT_WS_ENDPOINT=" not in proc.stdout
    assert "RADON_WEEKEND_BROWSER_HOST=unavailable:disabled" in proc.stdout


@pytest.mark.parametrize("name", WRAPPERS)
def test_scheduled_wrapper_contains_no_host_server_launcher(name):
    source = (REPO / "scripts" / f"{name}.sh").read_text()
    assert "launchServer" not in source
    assert "run-server" not in source
    assert "export PW_TEST_CONNECT_WS_ENDPOINT=" not in source
