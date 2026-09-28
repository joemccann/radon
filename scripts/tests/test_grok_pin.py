"""Grok pin parse, version mismatch, and last-known-good fallback."""

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


def _write_pin(path: Path, payload: dict | None = None) -> Path:
    target = path / "grok_pin.json"
    target.write_text(json.dumps(payload or PIN), encoding="utf-8")
    return target


class FakeRunner:
    def __init__(self, version="grok 1.0.41 (abc) [stable]", models=None, fail=None):
        self.version = version
        self.models = models or (
            "Default model: grok-4.7\n"
            "  grok-4.7 (default)\n"
            "  grok-4.7-build-fast\n"
            "  grok-4.6\n"
            "  grok-4.5\n"
        )
        self.fail = fail or set()
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        key = argv[1] if len(argv) > 1 else argv[0]
        if key in self.fail or (key == "--version" and "version" in self.fail):
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        if "--version" in argv:
            return SimpleNamespace(returncode=0, stdout=self.version, stderr="")
        if "models" in argv:
            return SimpleNamespace(returncode=0, stdout=self.models, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")


class TestLoadPin:
    def test_reads_checked_in_fields(self, tmp_path):
        pin = grok_pin.load_pin(_write_pin(tmp_path))
        assert pin.model == "grok-4.7"
        assert pin.reasoning_effort == "high"
        assert pin.cli_version == "1.0.41"
        assert pin.lkg_cli_version == "1.0.3"

    def test_rejects_missing_model(self, tmp_path):
        bad = dict(PIN)
        bad.pop("model")
        with pytest.raises(grok_pin.GrokPinError, match="model"):
            grok_pin.load_pin(_write_pin(tmp_path, bad))

    def test_repo_pin_file_is_valid(self):
        pin = grok_pin.load_pin()
        assert pin.model == "grok-4.7"
        assert pin.cli_version == "1.0.41"
        assert pin.lkg_cli_version == "1.0.3"


class TestResolve:
    def test_uses_pin_when_cli_and_model_match(self, tmp_path):
        pin = grok_pin.load_pin(_write_pin(tmp_path))
        resolved = grok_pin.resolve_runtime(pin, grok_bin="grok", runner=FakeRunner())
        assert resolved.refused is False
        assert resolved.used_fallback is False
        assert resolved.model == "grok-4.7"
        assert resolved.cli_version == "1.0.41"
        assert resolved.warning is None

    def test_cli_mismatch_falls_back_to_last_known_good(self, tmp_path):
        pin = grok_pin.load_pin(_write_pin(tmp_path))
        resolved = grok_pin.resolve_runtime(
            pin,
            grok_bin="grok",
            runner=FakeRunner(version="grok 1.0.3 (old) [stable]"),
        )
        assert resolved.refused is False
        assert resolved.used_fallback is True
        assert resolved.cli_version == "1.0.3"
        assert resolved.model == "grok-4.7"
        assert "1.0.3" in (resolved.warning or "")

    def test_missing_pinned_model_falls_back(self, tmp_path):
        pin = grok_pin.load_pin(_write_pin(tmp_path))
        models = (
            "Default model: grok-4.6\n"
            "  grok-4.6 (default)\n"
            "  grok-4.5\n"
        )
        # LKG model is also grok-4.7, so this refuses rather than unpinned default.
        resolved = grok_pin.resolve_runtime(
            pin, grok_bin="grok", runner=FakeRunner(models=models)
        )
        assert resolved.refused is True
        assert resolved.used_fallback is True
        assert "unpinned" in (resolved.warning or "")

    def test_probe_failure_refuses_unpinned_default(self, tmp_path):
        pin = grok_pin.load_pin(_write_pin(tmp_path))
        resolved = grok_pin.resolve_runtime(
            pin,
            grok_bin="grok",
            runner=FakeRunner(fail={"--version"}),
        )
        assert resolved.refused is True
        assert "unpinned" in (resolved.warning or "")


class TestParse:
    def test_parse_cli_version(self):
        assert grok_pin.parse_cli_version("grok 1.0.41 (4220f3b224a6) [stable]") == "1.0.41"

    def test_parse_models_listing(self):
        default, available = grok_pin.parse_models_listing(
            "Default model: grok-4.7\n  grok-4.7 (default)\n  grok-4.6\n"
        )
        assert default == "grok-4.7"
        assert "grok-4.7" in available and "grok-4.6" in available
