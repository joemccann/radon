"""Track-latest resolve, LKG IO, runtime fallback, and the promote lock."""

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


def _lkg(path: Path, **overrides) -> Path:
    payload = {
        "cli_version": "1.0.3",
        "binary_path": str(path / "lkg-grok"),
        "model": "grok-4.6",
        "reasoning_effort": "high",
        "promoted_at": "2026-09-01T00:00:00Z",
        "smoke_result": "stand_down: previous smoke",
    }
    payload.update(overrides)
    target = path / "grok_lkg.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


class FakeRunner:
    def __init__(self, version="grok 1.0.41 (abc) [stable]", models=None, fail=None):
        self.version = version
        self.models = models or (
            "Default model: grok-4.7\n"
            "  grok-4.7 (default)\n"
            "  grok-4.6\n"
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


class TestLkgIo:
    def test_round_trip(self, tmp_path):
        path = _lkg(tmp_path)
        state = grok_runtime.load_lkg(path)
        assert state is not None
        assert state.model == "grok-4.6"
        assert state.cli_version == "1.0.3"
        written = tmp_path / "out.json"
        grok_runtime.write_lkg(written, state)
        again = grok_runtime.load_lkg(written)
        assert again == state

    def test_missing_file_is_none(self, tmp_path):
        assert grok_runtime.load_lkg(tmp_path / "absent.json") is None

    def test_missing_model_raises(self, tmp_path):
        path = _lkg(tmp_path)
        raw = json.loads(path.read_text())
        raw.pop("model")
        path.write_text(json.dumps(raw))
        with pytest.raises(grok_runtime.GrokRuntimeError, match="model"):
            grok_runtime.load_lkg(path)


class TestResolveLatest:
    def test_uses_grok_models_default_and_always_names_it(self):
        resolved = grok_runtime.resolve_latest(
            grok_bin="grok", runner=FakeRunner()
        )
        assert resolved.refused is False
        assert resolved.used_fallback is False
        assert resolved.model == "grok-4.7"
        assert resolved.cli_version == "1.0.41"
        assert resolved.source == "models"
        assert resolved.binary_path == "grok"

    def test_probe_failure_falls_back_to_lkg(self, tmp_path):
        lkg = grok_runtime.load_lkg(_lkg(tmp_path))
        resolved = grok_runtime.resolve_latest(
            grok_bin="grok",
            runner=FakeRunner(fail={"--version"}),
            lkg=lkg,
        )
        assert resolved.used_fallback is True
        assert resolved.refused is False
        assert resolved.model == "grok-4.6"
        assert resolved.cli_version == "1.0.3"
        assert resolved.source == "lkg"

    def test_probe_failure_without_lkg_refuses(self):
        resolved = grok_runtime.resolve_latest(
            grok_bin="grok",
            runner=FakeRunner(fail={"--version"}),
        )
        assert resolved.refused is True
        assert resolved.model == ""

    def test_no_default_model_uses_lkg(self, tmp_path):
        lkg = grok_runtime.load_lkg(_lkg(tmp_path))
        resolved = grok_runtime.resolve_latest(
            grok_bin="grok",
            runner=FakeRunner(models="  grok-4.5\n"),
            lkg=lkg,
        )
        assert resolved.used_fallback is True
        assert resolved.model == "grok-4.6"


class TestFallback:
    def test_nonzero_exit_retries(self):
        assert grok_runtime.should_retry_lkg(1, "", "exit") is True

    def test_model_unavailable_retries(self):
        assert grok_runtime.should_retry_lkg(
            0, "", "model grok-4.8 is unavailable"
        ) is True

    def test_unparseable_output_retries(self):
        assert grok_runtime.should_retry_lkg(0, "no result line here", "") is True

    def test_valid_result_does_not_retry(self):
        assert grok_runtime.should_retry_lkg(
            0, "RESULT: stand_down | ok", ""
        ) is False


class TestLock:
    def test_nonblocking_lock_busy_raises(self, tmp_path, monkeypatch):
        import fcntl

        lock = tmp_path / "runtime.lock"
        held = []

        def _flock(fd, flags):
            if flags & fcntl.LOCK_NB:
                raise BlockingIOError("busy")
            held.append(flags)

        monkeypatch.setattr(grok_runtime.fcntl, "flock", _flock)
        with pytest.raises(grok_runtime.GrokRuntimeError, match="lock busy"):
            with grok_runtime.exclusive_lock(lock, blocking=False):
                pass

    def test_blocking_lock_holds_then_releases(self, tmp_path):
        lock = tmp_path / "runtime.lock"
        with grok_runtime.exclusive_lock(lock, blocking=True):
            assert lock.exists()


class TestStamp:
    def test_round_trip_stamp(self):
        line = grok_runtime.runtime_stamp("grok-4.7", "1.0.41")
        assert grok_runtime.extract_runtime_stamp(line) == ("grok-4.7", "1.0.41")

    def test_parse_cli_version(self):
        assert grok_runtime.parse_cli_version(
            "grok 1.0.41 (4220f3b224a6) [stable]"
        ) == "1.0.41"



def test_default_runtime_lock_lives_in_its_own_dir(monkeypatch):
    """T-518: always check the sandbox-compatible default, even on custom hosts."""
    import runpy

    monkeypatch.delenv("RADON_GROK_RUNTIME_LOCK", raising=False)
    # Re-evaluate import-time configuration without mutating the shared module.
    runtime = runpy.run_path(grok_runtime.__file__)
    assert str(runtime["DEFAULT_LOCK_PATH"]) == (
        "/var/lib/radon/grok-runtime/grok-runtime.lock"
    )


def test_runtime_lock_honors_explicit_override(monkeypatch, tmp_path):
    import runpy

    lock = tmp_path / "custom-runtime.lock"
    monkeypatch.setenv("RADON_GROK_RUNTIME_LOCK", str(lock))
    runtime = runpy.run_path(grok_runtime.__file__)
    assert runtime["DEFAULT_LOCK_PATH"] == lock
