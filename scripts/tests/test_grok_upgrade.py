"""Promote-on-pass and stay-on-fail for the daily Grok upgrader."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_runtime  # noqa: E402
import grok_upgrade as upgrade  # noqa: E402

VALID_BODY = """## What broke
The daily grok upgrade smoke ran against a candidate CLI on a scratch tree.
Page smoke-check, first seen in this dry-run, no production alert.
## Root cause
xAI shipped a newer stable CLI or default model than last-known-good.
## What changed
- live grok symlink: candidate CLI promoted after the smoke passed.
## How it was verified
Canned dry-run returned RESULT stand_down and this structured body.
## Risk and rollback
Rollback restores the previous last-known-good binary path. Incident runs keep --no-auto-update.
## Still open
Nothing else. The smoke is a dry-run and does not edit the responder clone.
"""

LKG = {
    "cli_version": "1.0.3",
    "binary_path": "/tmp/lkg-grok",
    "model": "grok-4.7",
    "reasoning_effort": "high",
    "promoted_at": "2026-09-01T00:00:00Z",
    "smoke_result": "stand_down: previous",
}

MODELS = "Default model: grok-4.7\n  grok-4.7 (default)\n  grok-4.8\n"


class ScriptedRunner:
    def __init__(self, mapping: dict[tuple[str, ...], SimpleNamespace]):
        self.mapping = mapping
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        for prefix, proc in self.mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return proc
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected: " + " ".join(argv))


def _lkg(tmp_path: Path) -> Path:
    path = tmp_path / "grok_lkg.json"
    payload = dict(LKG)
    payload["binary_path"] = str(tmp_path / "live" / "grok")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestDecide:
    def test_no_upgrade_when_lkg_matches_latest(self, tmp_path):
        lkg = grok_runtime.load_lkg(_lkg(tmp_path))
        assert (
            upgrade.decide_upgrade(
                lkg=lkg,
                update_check={"current": "1.0.3", "latest": "1.0.3"},
                models_stdout=MODELS,
            )
            is None
        )

    def test_upgrades_when_stable_cli_is_newer(self, tmp_path):
        lkg = grok_runtime.load_lkg(_lkg(tmp_path))
        decision = upgrade.decide_upgrade(
            lkg=lkg,
            update_check={"current": "1.0.3", "latest": "1.0.50"},
            models_stdout=MODELS,
        )
        assert decision is not None
        assert decision["cli_version"] == "1.0.50"
        assert decision["model"] == "grok-4.7"

    def test_upgrades_when_default_model_moved(self, tmp_path):
        lkg = grok_runtime.load_lkg(_lkg(tmp_path))
        decision = upgrade.decide_upgrade(
            lkg=lkg,
            update_check={"current": "1.0.3", "latest": "1.0.3"},
            models_stdout="Default model: grok-4.8\n  grok-4.8 (default)\n  grok-4.7\n",
        )
        assert decision is not None
        assert decision["model"] == "grok-4.8"

    def test_parse_update_check_json(self):
        parsed = upgrade.parse_update_check(
            json.dumps({"current": "1.0.3", "latest": "1.0.41"})
        )
        assert parsed["latest"] == "1.0.41"


class TestRunUpgrade:
    def test_current_is_a_noop(self, tmp_path):
        lkg_path = _lkg(tmp_path)
        runner = ScriptedRunner({
            ("grok", "update", "--check"): SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"current": "1.0.3", "latest": "1.0.3"}),
                stderr="",
            ),
            ("grok", "models"): SimpleNamespace(returncode=0, stdout=MODELS, stderr=""),
        })
        result = upgrade.run_upgrade(
            grok_bin="grok",
            lkg_path=lkg_path,
            scratch=tmp_path / "scratch",
            runner=runner,
        )
        assert result["action"] == "current"
        assert not any("update" in c and "--version" in c for c in runner.calls)

    def test_failed_smoke_alerts_and_leaves_lkg(self, tmp_path):
        lkg_path = _lkg(tmp_path)
        alerts: list[str] = []
        runner = ScriptedRunner({
            ("grok", "update", "--check"): SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"current": "1.0.3", "latest": "1.0.50"}),
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
        result = upgrade.run_upgrade(
            grok_bin="grok",
            lkg_path=lkg_path,
            scratch=tmp_path / "scratch",
            runner=runner,
            alerter=alerts.append,
        )
        assert result["action"] == "failed"
        assert alerts
        assert json.loads(lkg_path.read_text())["cli_version"] == "1.0.3"

    def test_green_smoke_promotes_and_writes_lkg(self, tmp_path):
        lkg_path = _lkg(tmp_path)
        live = tmp_path / "live" / "grok"
        live.parent.mkdir()
        live.write_text("old\n", encoding="utf-8")
        candidate = tmp_path / "scratch" / "candidate" / "bin"
        candidate.mkdir(parents=True)
        (candidate / "grok").write_text("new\n", encoding="utf-8")
        runner = ScriptedRunner({
            ("grok", "update", "--check"): SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"current": "1.0.3", "latest": "1.0.50"}),
                stderr="",
            ),
            ("grok", "models"): SimpleNamespace(returncode=0, stdout=MODELS, stderr=""),
            ("grok", "update", "--version"): SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        })

        def _run(argv, **kwargs):
            if argv and str(argv[0]).endswith("/grok") and "--prompt-file" in argv:
                runner.calls.append(list(argv))
                return SimpleNamespace(
                    returncode=0,
                    stdout=VALID_BODY + "\nRESULT: stand_down | grok upgrade smoke structured\n",
                    stderr="",
                )
            return runner(argv, **kwargs)

        result = upgrade.run_upgrade(
            grok_bin="grok",
            live_bin=live,
            lkg_path=lkg_path,
            lock_path=tmp_path / "runtime.lock",
            scratch=tmp_path / "scratch",
            runner=_run,
        )
        assert result["action"] == "promoted"
        written = json.loads(lkg_path.read_text())
        assert written["cli_version"] == "1.0.50"
        assert written["model"] == "grok-4.7"
        assert live.is_symlink()
        assert live.resolve() == (candidate / "grok").resolve()

    def test_lock_busy_skips_promote(self, tmp_path, monkeypatch):
        lkg_path = _lkg(tmp_path)
        live = tmp_path / "live" / "grok"
        live.parent.mkdir()
        live.write_text("old\n", encoding="utf-8")
        candidate = tmp_path / "scratch" / "candidate" / "bin"
        candidate.mkdir(parents=True)
        (candidate / "grok").write_text("new\n", encoding="utf-8")
        runner = ScriptedRunner({
            ("grok", "update", "--check"): SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"current": "1.0.3", "latest": "1.0.50"}),
                stderr="",
            ),
            ("grok", "models"): SimpleNamespace(returncode=0, stdout=MODELS, stderr=""),
            ("grok", "update", "--version"): SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        })

        def _run(argv, **kwargs):
            if argv and str(argv[0]).endswith("/grok") and "--prompt-file" in argv:
                return SimpleNamespace(
                    returncode=0,
                    stdout=VALID_BODY + "\nRESULT: stand_down | grok upgrade smoke structured\n",
                    stderr="",
                )
            return runner(argv, **kwargs)

        def _busy(*_a, **_k):
            raise grok_runtime.GrokRuntimeError("grok runtime lock busy: x")

        monkeypatch.setattr(grok_runtime, "exclusive_lock", _busy)
        result = upgrade.run_upgrade(
            grok_bin="grok",
            live_bin=live,
            lkg_path=lkg_path,
            lock_path=tmp_path / "runtime.lock",
            scratch=tmp_path / "scratch",
            runner=_run,
        )
        assert result["action"] == "locked"
        assert json.loads(lkg_path.read_text())["cli_version"] == "1.0.3"
        assert not live.is_symlink()


class TestSeed:
    def test_seed_writes_lkg_from_live_cli(self, tmp_path):
        lkg_path = tmp_path / "grok_lkg.json"
        runner = ScriptedRunner({
            ("grok", "--version"): SimpleNamespace(
                returncode=0, stdout="grok 1.0.41 (abc) [stable]", stderr=""
            ),
            ("grok", "models"): SimpleNamespace(returncode=0, stdout=MODELS, stderr=""),
        })
        state = upgrade.seed_lkg_if_missing(
            grok_bin="grok", lkg_path=lkg_path, runner=runner
        )
        assert state is not None
        assert state.model == "grok-4.7"
        assert state.cli_version == "1.0.41"
        again = upgrade.seed_lkg_if_missing(
            grok_bin="grok", lkg_path=lkg_path, runner=runner
        )
        assert again == state
        assert sum(1 for c in runner.calls if "--version" in c) == 1
