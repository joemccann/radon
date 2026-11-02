"""Client export time limits track progress, not a fixed 60s wall clock.

CI evidence (App-plane images (PR), 2026-11-01/02): the Linux export streams
1,621,964,800 bytes and the Darwin export 700,866,560 bytes; both together
take 77-101s on ubuntu-24.04 runners (~23-30 MB/s). The Linux export alone
sat at ~55s against the old fixed 60s deadline and failed 3 of 28 attempts
with "TimeoutError: client export deadline" while bytes were still flowing.
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "cloud/scripts/model_clients.py"
CHECKER = REPO / "scripts/ci/check_model_client_bounds.py"
ROOT_HELPER = REPO / "cloud/scripts/deploy-root-helper.sh"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api():
    return load(MODULE, "model_clients_export_deadline")


def trickle_engine(tmp_path: Path, *, chunks: int, interval: float, initial_delay: float = 0.0) -> str:
    """A fake engine whose `run` writes `chunks` 1 KiB chunks `interval` apart."""
    engine = tmp_path / "engine"
    engine.write_text(
        f"#!{sys.executable}\nimport sys, time\n"
        "if sys.argv[1] != 'run': raise SystemExit(0)\n"
        f"time.sleep({initial_delay})\n"
        f"for _ in range({chunks}):\n"
        "    sys.stdout.buffer.write(b'x' * 1024); sys.stdout.buffer.flush()\n"
        f"    time.sleep({interval})\n"
    )
    engine.chmod(0o755)
    return str(engine)


def test_export_keeps_going_past_the_stall_window_while_bytes_flow(api, tmp_path, monkeypatch):
    # Total runtime (~1.6s) is several stall windows; progress keeps it alive.
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 0.5)
    monkeypatch.setattr(api, "EXPORT_CEILING_SECONDS", 10)
    target = io.BytesIO()
    api.export_from_image(trickle_engine(tmp_path, chunks=8, interval=0.2), "image", target)
    assert target.read() == b"x" * 8 * 1024


def test_export_fails_fast_with_an_accurate_message_when_bytes_stop(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 0.5)
    monkeypatch.setattr(api, "EXPORT_CEILING_SECONDS", 10)
    engine = trickle_engine(tmp_path, chunks=2, interval=5)
    started = time.monotonic()
    with pytest.raises(TimeoutError) as raised:
        api.export_from_image(engine, "image", io.BytesIO())
    assert time.monotonic() - started < 4
    message = str(raised.value)
    assert "stalled" in message and "1024 bytes" in message


def test_export_with_no_first_byte_is_a_stall(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 0.5)
    monkeypatch.setattr(api, "EXPORT_CEILING_SECONDS", 10)
    with pytest.raises(TimeoutError, match=r"stalled.* 0 bytes"):
        api.export_from_image(trickle_engine(tmp_path, chunks=1, interval=0, initial_delay=5), "image", io.BytesIO())


def test_steady_progress_still_hits_the_hard_ceiling(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 0.5)
    monkeypatch.setattr(api, "EXPORT_CEILING_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="ceiling") as raised:
        api.export_from_image(trickle_engine(tmp_path, chunks=1000, interval=0.1), "image", io.BytesIO())
    assert time.monotonic() - started < 4
    assert re.search(r"after \d+ bytes", str(raised.value))


def test_byte_bounds_still_refuse_a_fast_oversized_export(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "LINUX_MAX_STREAM_BYTES", 4096)
    with pytest.raises(ValueError, match="exceeds bounds"):
        api.export_from_image(trickle_engine(tmp_path, chunks=8, interval=0), "image", io.BytesIO())


def test_limits_are_sized_from_ci_evidence_and_fit_the_root_action_budget(api):
    # Slowest observed CI throughput ~23 MB/s; the largest admissible Linux
    # stream must fit the ceiling with headroom at that rate.
    assert api.LINUX_MAX_STREAM_BYTES / 23e6 * 1.5 <= api.EXPORT_CEILING_SECONDS
    # Production runs the export inside restart-managed / promote-in-place,
    # whose root action budget kills the helper; the export's own, labelled
    # error must fire first.
    budget = int(re.search(r"readonly ROOT_MUTATION_ACTION_TIMEOUT=(\d+)\n", ROOT_HELPER.read_text()).group(1))
    assert api.EXPORT_CEILING_SECONDS < budget
    assert 0 < api.EXPORT_STALL_SECONDS < api.EXPORT_CEILING_SECONDS


def test_ci_checker_does_not_tell_people_to_raise_pins_for_a_timeout(tmp_path, monkeypatch, capsys):
    checker = load(CHECKER, "check_model_client_bounds_deadline")
    real = checker.load_model_clients

    def fast():
        module = real()
        module.EXPORT_STALL_SECONDS = 0.5
        return module

    monkeypatch.setattr(checker, "load_model_clients", fast)
    engine = trickle_engine(tmp_path, chunks=1, interval=0, initial_delay=5)
    assert checker.main(["--engine", engine, "--image", "image", "--scratch", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "TimeoutError" in err and "stalled" in err
    assert "Raise the pins" not in err


def test_ci_checker_still_points_at_the_pins_for_a_bounds_breach(tmp_path, monkeypatch, capsys):
    checker = load(CHECKER, "check_model_client_bounds_breach")
    real = checker.load_model_clients

    def tight():
        module = real()
        module.LINUX_MAX_STREAM_BYTES = 2048
        return module

    monkeypatch.setattr(checker, "load_model_clients", tight)
    engine = trickle_engine(tmp_path, chunks=8, interval=0)
    assert checker.main(["--engine", engine, "--image", "image", "--scratch", str(tmp_path)]) == 1
    assert "Raise the pins" in capsys.readouterr().err
