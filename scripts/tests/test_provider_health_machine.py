"""Model-ladder provider health: cooldowns only extend, trips are never lost.

Audit implicit-state-machines-20261103 §3.4 "Model-ladder provider health":
three mechanisms (provider-cooldown file, engine dead-rung memory, NVIDIA
pacing file + ``.tripped`` sidecar) each lost or weakened state somewhere.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import nvidia_rate_limit as nrl
import pytest
from clients import provider_cooldown
from model_gateway import cache_migrate

REPO = Path(__file__).resolve().parents[2]


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, secs: float) -> None:
        self.now += secs


def _limiter(tmp_path, clock, **kw):
    kw.setdefault("rpm", 30)
    return nrl.RateLimiter(tmp_path / "rate.json", clock=clock, sleep=clock.sleep, **kw)


# --- provider cooldown file ----------------------------------------------------

class TestProviderCooldown:
    def _env(self, tmp_path):
        return {"RADON_PROVIDER_COOLDOWN_STATE": str(tmp_path / "cooldown.json")}

    def test_a_shorter_cooldown_never_overwrites_a_longer_one(self, tmp_path):
        env = self._env(tmp_path)
        provider_cooldown.note("codex", 3 * 86400, env)
        # A second oneshot (fresh process memory) sees a short burst 429.
        provider_cooldown.clear_process_state()
        provider_cooldown.note("codex", 60, env)
        stored = json.loads(Path(env["RADON_PROVIDER_COOLDOWN_STATE"]).read_text())
        assert stored["codex"] > provider_cooldown._now() + 2 * 86400

    def test_clear_drops_the_persisted_and_process_cooldown(self, tmp_path):
        env = self._env(tmp_path)
        provider_cooldown.note("codex", 3600, env)
        provider_cooldown.note("grok", 3600, env)
        provider_cooldown.clear("codex", env)
        assert provider_cooldown.rate_limited("codex", env) is False
        provider_cooldown.clear_process_state()
        assert provider_cooldown.rate_limited("codex", env) is False
        assert provider_cooldown.rate_limited("grok", env) is True

    def test_clear_without_a_state_file_is_a_noop(self, tmp_path):
        provider_cooldown.clear("codex", self._env(tmp_path))
        assert not (tmp_path / "cooldown.json").exists()


def test_engine_clears_the_cooldown_when_a_provider_wins():
    source = (REPO / "scripts/model_gateway/engine.py").read_text()
    won = source.split("if provider_won:\n            _dead_until.pop(name, None)", 1)
    assert len(won) == 2, "engine success branch moved; update this pin"
    assert "provider_cooldown.clear(name, env)" in won[1].split("elif", 1)[0]


# --- NVIDIA pacing file --------------------------------------------------------

class TestNvidiaTrip:
    def test_trip_expiry_needs_a_fresh_streak_to_trip_again(self, tmp_path):
        clock = FakeClock()
        lim = _limiter(tmp_path, clock, trip_after=3, trip_secs=900)
        for _ in range(3):
            lim.note_429(retry_after=1)
        assert lim.tripped_until() > 0
        clock.now += 901
        assert lim.tripped_until() == 0
        lim.note_429(retry_after=1)
        assert lim.tripped_until() == 0, "one 429 after expiry must not re-trip"

    def test_acquire_honours_the_tripped_sidecar(self, tmp_path):
        clock = FakeClock()
        lim = _limiter(tmp_path, clock)
        (tmp_path / "rate.json.tripped").write_text(f"{int(clock.now + 600)}\n")
        assert lim.acquire(max_wait=0) is False
        assert lim.tripped_until() == pytest.approx(int(clock.now + 600))

    def test_corrupt_state_keeps_the_sidecar_trip(self, tmp_path):
        clock = FakeClock()
        lim = _limiter(tmp_path, clock, trip_secs=900)
        lim.note_auth_failure(403)
        (tmp_path / "rate.json").write_text("{not json")
        assert lim.acquire(max_wait=0) is False

    def test_corrupt_state_fails_toward_tripped_for_a_bounded_time(self, tmp_path):
        clock = FakeClock()
        lim = _limiter(tmp_path, clock, trip_secs=900)
        (tmp_path / "rate.json").write_text("{not json")
        assert lim.acquire(max_wait=0) is False
        clock.now += 901
        assert lim.acquire(max_wait=0) is True
        # The rewrite left a parseable file, so every reader agrees again.
        json.loads((tmp_path / "rate.json").read_text())

    def test_a_failed_state_write_still_paces_this_process(self, tmp_path, monkeypatch):
        clock = FakeClock()
        lim = _limiter(tmp_path, clock, trip_after=99)
        real_replace = os.replace

        def refuse(src, dst, *a, **k):
            if str(dst).endswith("rate.json"):
                raise OSError("read-only")
            return real_replace(src, dst, *a, **k)

        monkeypatch.setattr(nrl.os, "replace", refuse)
        lim.note_429(retry_after=120)
        assert lim.acquire(max_wait=0) is False, "the cooldown must not be lost"
        monkeypatch.setattr(nrl.os, "replace", real_replace)
        clock.now += 121
        assert lim.acquire(max_wait=0) is True


# --- readers stay in parity ----------------------------------------------------

def _capture_reader():
    spec = importlib.util.spec_from_file_location(
        "pacing_capture", REPO / "cloud/scripts/model_pacing.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_reader_sees_the_same_trip_deadline(tmp_path):
    """Limiter, root capture reader and cache migration agree on tripped_until."""
    clock = FakeClock()
    lim = _limiter(tmp_path, clock, trip_after=2, trip_secs=900)
    lim.note_429(retry_after=1)
    lim.note_429(retry_after=1)
    # The sidecar alone carries a later trip (another host's auth failure).
    later = int(clock.now + 1800)
    (tmp_path / "rate.json.tripped").write_text(f"{later}\n")
    for name in ("rate.json", "rate.json.tripped"):
        (tmp_path / name).chmod(0o600)

    limiter_view = lim.tripped_until()

    result = subprocess.run(
        [sys.executable, "-I", "-c", _capture_reader().READER],
        env={**os.environ, "RADON_NVIDIA_RATE_STATE": str(tmp_path / "rate.json")},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    capture_view = json.loads(result.stdout)["tripped_until"]

    destination = tmp_path / "dest" / "nvidia-rate.json"
    destination.parent.mkdir(mode=0o700)
    cache_migrate.migrate_pacing([tmp_path / "rate.json"], destination)
    migrate_view = json.loads(destination.read_text())["tripped_until"]

    assert limiter_view == capture_view == migrate_view == later
    # The trip reset the streak, and every reader accepts the reset counter.
    assert json.loads((tmp_path / "rate.json").read_text())["consecutive_429"] == 0
