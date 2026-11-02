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
import subprocess
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
    monkeypatch.setattr(api, "LINUX_EXPORT_CEILING_SECONDS", 10)
    target = io.BytesIO()
    api.export_from_image(trickle_engine(tmp_path, chunks=8, interval=0.2), "image", target)
    assert target.read() == b"x" * 8 * 1024


def test_export_fails_fast_with_an_accurate_message_when_bytes_stop(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 0.5)
    monkeypatch.setattr(api, "LINUX_EXPORT_CEILING_SECONDS", 10)
    engine = trickle_engine(tmp_path, chunks=2, interval=5)
    started = time.monotonic()
    with pytest.raises(TimeoutError) as raised:
        api.export_from_image(engine, "image", io.BytesIO())
    assert time.monotonic() - started < 4
    message = str(raised.value)
    assert "stalled" in message and "1024 bytes" in message


def test_export_with_no_first_byte_is_a_stall(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 0.5)
    monkeypatch.setattr(api, "LINUX_EXPORT_CEILING_SECONDS", 10)
    with pytest.raises(TimeoutError, match=r"stalled.* 0 bytes"):
        api.export_from_image(trickle_engine(tmp_path, chunks=1, interval=0, initial_delay=5), "image", io.BytesIO())


def test_steady_progress_still_hits_the_hard_ceiling(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 0.5)
    monkeypatch.setattr(api, "LINUX_EXPORT_CEILING_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="ceiling") as raised:
        api.export_from_image(trickle_engine(tmp_path, chunks=1000, interval=0.1), "image", io.BytesIO())
    assert time.monotonic() - started < 4
    assert re.search(r"after \d+ bytes", str(raised.value))


def test_byte_bounds_still_refuse_a_fast_oversized_export(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "LINUX_MAX_STREAM_BYTES", 4096)
    with pytest.raises(ValueError, match="exceeds bounds"):
        api.export_from_image(trickle_engine(tmp_path, chunks=8, interval=0), "image", io.BytesIO())


def test_darwin_export_uses_its_own_ceiling(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 0.5)
    monkeypatch.setattr(api, "LINUX_EXPORT_CEILING_SECONDS", 10)
    monkeypatch.setattr(api, "DARWIN_EXPORT_CEILING_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="1s ceiling"):
        api.export_from_image(trickle_engine(tmp_path, chunks=1000, interval=0.1), "image", io.BytesIO(), darwin=True)
    assert time.monotonic() - started < 4


def test_limits_are_sized_from_ci_evidence(api):
    # Slowest observed CI throughput ~23 MB/s; the largest admissible stream
    # of each flavour must fit its ceiling with headroom at that rate. CI runs
    # the exports standalone, outside any root action, so only these apply.
    assert api.LINUX_MAX_STREAM_BYTES / 23e6 * 1.25 <= api.LINUX_EXPORT_CEILING_SECONDS
    assert api.DARWIN_MAX_EXPORT_BYTES / 23e6 * 1.25 <= api.DARWIN_EXPORT_CEILING_SECONDS
    assert 0 < api.EXPORT_STALL_SECONDS < min(api.LINUX_EXPORT_CEILING_SECONDS, api.DARWIN_EXPORT_CEILING_SECONDS)
    # The post-export reserve must leave most of the real root budget to the
    # exports: production promote-in-place took 84-98s end to end.
    budget = int(re.search(r"readonly ROOT_MUTATION_ACTION_TIMEOUT=(\d+)\n", ROOT_HELPER.read_text()).group(1))
    assert 0 < api.POST_EXPORT_RESERVE_SECONDS and budget - api.POST_EXPORT_RESERVE_SECONDS >= 120


def test_root_deadline_binds_before_the_flavour_ceiling(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EXPORT_STALL_SECONDS", 5)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match=r"root action deadline after \d+ bytes"):
        api.export_from_image(trickle_engine(tmp_path, chunks=1000, interval=0.1), "image", io.BytesIO(),
                              deadline=started + 1)
    assert time.monotonic() - started < 4


def test_no_time_left_refuses_before_starting_an_export(api, tmp_path):
    marker = tmp_path / "ran"
    engine = tmp_path / "engine"
    engine.write_text(f"#!/bin/sh\ntouch {marker}\n")
    engine.chmod(0o755)
    with pytest.raises(TimeoutError, match="root action deadline"):
        api.export_from_image(str(engine), "image", io.BytesIO(), deadline=time.monotonic() - 1)
    assert not marker.exists()


@pytest.mark.parametrize("raw", ["", "soon", "-5", "1e9", "12.5"])
def test_malformed_root_deadline_refuses_promotion(api, monkeypatch, raw):
    monkeypatch.setattr(api.os, "geteuid", lambda: 0)
    monkeypatch.setenv("RADON_ROOT_ACTION_DEADLINE", raw)
    monkeypatch.setattr(api, "check_model_space", lambda: pytest.fail("admission ran"))
    assert api.main(["/usr/bin/docker", "ghcr.io/joemccann/radon-model-gateway:" + "a" * 40, "a" * 40]) == 64


def test_complete_root_action_fails_with_the_labelled_timeout_before_the_supervisor(tmp_path):
    """promote-in-place end to end: supervisor -> helper -> runtime -> main().

    Slow admission work eats part of the budget and the export keeps flowing
    bytes, so neither the stall window nor the 115s flavour ceiling can fire.
    Only a deadline derived from the supervisor's own budget stops the export
    with its labelled error before the supervisor kills the action (rc 124).
    """
    from test_deploy_corrections import TestRootHelper

    env, *_ = TestRootHelper()._root_helper_fixture(tmp_path)
    engine = trickle_engine(tmp_path, chunks=100000, interval=0.05)
    clients, exports = tmp_path / "clients", tmp_path / "exports"
    clients.mkdir()
    exports.mkdir()
    wrapper = tmp_path / "model_clients_wrapper.py"
    wrapper.write_text(
        "import importlib.util, sys, time\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('mc', {str(MODULE)!r})\n"
        "api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)\n"
        "api.os.geteuid = lambda: 0\n"
        f"api.ROOT, api.EXPORT_ROOT = Path({str(clients)!r}), Path({str(exports)!r})\n"
        "api.check_model_space = lambda: time.sleep(2) or {}\n"
        "api._trusted_root = lambda *a, **k: None\n"
        "api.reuse_identical_image_clients = lambda *a, **k: False\n"
        "api.POST_EXPORT_RESERVE_SECONDS = 1\n"
        "real = api.export_from_image\n"
        f"api.export_from_image = lambda _engine, *a, **k: real({engine!r}, *a, **k)\n"
        "sha = 'a' * 40\n"
        "raise SystemExit(api.main(['/usr/bin/docker', 'ghcr.io/joemccann/radon-model-gateway:' + sha, sha]))\n"
    )
    runtime = tmp_path / "model-client-runtime"
    runtime.write_text(
        "#!/bin/bash\n"
        f'[[ "$1" == install-model-clients ]] || exit 0\nexec {sys.executable} {wrapper}\n'
    )
    runtime.chmod(0o755)
    budget = 8
    env.update({
        "RADON_TEST_MODEL_CLIENT_RUNTIME": str(runtime),
        "RADON_TEST_ROOT_MUTATION_ACTION_TIMEOUT": str(budget),
        "RADON_TEST_COMMIT_SHA": "a" * 40,
    })
    started = time.monotonic()
    result = subprocess.run(["bash", str(ROOT_HELPER), "promote-in-place"], env=env,
                            capture_output=True, text=True, timeout=60)
    elapsed = time.monotonic() - started
    assert result.returncode == 78, (result.returncode, result.stderr)
    assert "TimeoutError" in result.stderr and "root action deadline" in result.stderr
    assert elapsed < budget, elapsed


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
