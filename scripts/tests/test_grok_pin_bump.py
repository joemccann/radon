"""Pin-bump decision and smoke/commit path, with subprocess mocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_pin  # noqa: E402
import grok_pin_bump as bump  # noqa: E402

VALID_BODY = """## What broke
The weekly pin check found a newer stable Grok CLI than the repo pin.
Page smoke-check, first seen in this dry-run, no production alert.
## Root cause
xAI moved the default CLI without a checked-in pin, so the VPS could drift.
## What changed
- config/grok_pin.json: cli_version moves to the smoked candidate.
## How it was verified
Canned dry-run returned RESULT stand_down and this structured body.
## Risk and rollback
Live ~/.local/bin/grok is untouched. Rollback restores last-known-good.
## Still open
Joe reviews the smoke output before merge. The timer stays disabled.
"""

PIN = {
    "model": "grok-4.7",
    "reasoning_effort": "high",
    "cli_version": "1.0.41",
    "last_known_good": {
        "model": "grok-4.7",
        "reasoning_effort": "high",
        "cli_version": "1.0.3",
    },
}

MODELS = "Default model: grok-4.7\n  grok-4.7 (default)\n  grok-4.8\n"


class ScriptedRunner:
    def __init__(self, mapping: dict[tuple[str, ...], SimpleNamespace]):
        self.mapping = mapping
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        key = tuple(argv[:3])
        for prefix, proc in self.mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return proc
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected: " + " ".join(argv))


def _pin(tmp_path: Path) -> Path:
    path = tmp_path / "config" / "grok_pin.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(PIN), encoding="utf-8")
    return path


class TestDecide:
    def test_no_bump_when_pin_matches_latest(self, tmp_path):
        pin = grok_pin.load_pin(_pin(tmp_path))
        assert (
            bump.decide_bump(
                pin,
                update_check={"current": "1.0.41", "latest": "1.0.41"},
                models_stdout=MODELS,
            )
            is None
        )

    def test_bumps_when_stable_cli_is_newer(self, tmp_path):
        pin = grok_pin.load_pin(_pin(tmp_path))
        decision = bump.decide_bump(
            pin,
            update_check={"current": "1.0.41", "latest": "1.0.50"},
            models_stdout=MODELS,
        )
        assert decision is not None
        assert decision["cli_version"] == "1.0.50"
        assert decision["model"] == "grok-4.7"

    def test_bumps_when_default_model_moved(self, tmp_path):
        pin = grok_pin.load_pin(_pin(tmp_path))
        decision = bump.decide_bump(
            pin,
            update_check={"current": "1.0.41", "latest": "1.0.41"},
            models_stdout="Default model: grok-4.8\n  grok-4.8 (default)\n  grok-4.7\n",
        )
        assert decision is not None
        assert decision["model"] == "grok-4.8"
        assert decision["cli_version"] == "1.0.41"

    def test_parse_update_check_json(self):
        parsed = bump.parse_update_check(
            json.dumps({"current": "1.0.3", "latest": "1.0.41"})
        )
        assert parsed["latest"] == "1.0.41"


class TestRunBump:
    def test_current_pin_is_a_noop(self, tmp_path):
        pin_path = _pin(tmp_path)
        runner = ScriptedRunner({
            ("grok", "update", "--check"): SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"current": "1.0.41", "latest": "1.0.41"}),
                stderr="",
            ),
            ("grok", "models"): SimpleNamespace(returncode=0, stdout=MODELS, stderr=""),
        })
        result = bump.run_bump(
            tmp_path, grok_bin="grok", pin_path=pin_path, scratch=tmp_path / "scratch",
            runner=runner,
        )
        assert result["action"] == "current"
        assert not any("update" in c and "--version" in c for c in runner.calls)

    def test_failed_smoke_alerts_and_leaves_the_pin(self, tmp_path):
        pin_path = _pin(tmp_path)
        alerts: list[str] = []
        runner = ScriptedRunner({
            ("grok", "update", "--check"): SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"current": "1.0.41", "latest": "1.0.50"}),
                stderr="",
            ),
            ("grok", "models"): SimpleNamespace(returncode=0, stdout=MODELS, stderr=""),
            ("grok", "update", "--version"): SimpleNamespace(returncode=0, stdout="ok", stderr=""),
            ("grok", "--prompt-file"): SimpleNamespace(
                returncode=0,
                stdout="RESULT: failed | smoke died\n",
                stderr="",
            ),
        })
        # install_side_cli looks up grok_bin + update --version; smoke uses
        # the same grok_bin when the side path is missing.
        result = bump.run_bump(
            tmp_path,
            grok_bin="grok",
            pin_path=pin_path,
            scratch=tmp_path / "scratch",
            runner=runner,
            alerter=alerts.append,
        )
        assert result["action"] == "failed"
        assert alerts
        assert json.loads(pin_path.read_text())["cli_version"] == "1.0.41"

    def test_green_smoke_commits_a_structured_pin_bump(self, tmp_path):
        pin_path = _pin(tmp_path)
        (tmp_path / "scratch" / "candidate" / "bin").mkdir(parents=True)
        (tmp_path / "scratch" / "candidate" / "bin" / "grok").write_text("#!/bin/sh\n", encoding="utf-8")
        git_ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        runner = ScriptedRunner({
            ("grok", "update", "--check"): SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"current": "1.0.41", "latest": "1.0.50"}),
                stderr="",
            ),
            ("grok", "models"): SimpleNamespace(returncode=0, stdout=MODELS, stderr=""),
            ("grok", "update", "--version"): SimpleNamespace(returncode=0, stdout="ok", stderr=""),
            ("git", "checkout", "-B"): git_ok,
            ("git", "add"): git_ok,
            ("git", "commit"): git_ok,
        })

        def _run(argv, **kwargs):
            if argv and argv[0].endswith("/grok") and "--prompt-file" in argv:
                runner.calls.append(list(argv))
                return SimpleNamespace(
                    returncode=0,
                    stdout=VALID_BODY + "\nRESULT: stand_down | grok pin smoke structured\n",
                    stderr="",
                )
            return runner(argv, **kwargs)

        result = bump.run_bump(
            tmp_path,
            grok_bin="grok",
            pin_path=pin_path,
            scratch=tmp_path / "scratch",
            runner=_run,
        )
        assert result["action"] == "committed"
        assert result["branch"].startswith("fix/grok-pin-")
        written = json.loads(pin_path.read_text())
        assert written["cli_version"] == "1.0.50"
        assert written["last_known_good"]["cli_version"] == "1.0.41"
