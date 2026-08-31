"""R-422 / R-423 / R-424 / REL-148: six isolated operability fixes (python half).

R-422: `_SCRIPT_SERVICES` maps `gex_scan.py -> "gex-scan"`, but `gex-scan` is in
neither `SCHEDULED_SERVICES` nor the derived error bucket, so `check.py` never
evaluates it -- and its only registration is `serviceHealthWindows.ts` as
`category: "on-demand"` even though this delta puts it on the 15-minute RTH
timer.

R-423: the timer child budget was raised to 180s because cri regularly needs
60-103s on a live IB path, but `/regime/scan` still spawned the same script at
`timeout=120` -- so the slow-IB runs the raise was made FOR are exactly the ones
the browser path SIGKILLs, arming the scan gate and 502ing the panel. And
`cri_scan.py` holds no lock, so a browser POST landing during a timer fire runs
a second scan concurrently against the same `data/cri.json` and IB client-id
range.

R-424: the 256-gate overflow path is correctly fail-closed, but when the map
saturates the only externally visible artifact is a 429 whose detail reads
"backing off after a failure": no log line, no `service_health` row, nothing
naming saturation -- and `_evict_idle_scan_gate` cannot evict while every
subject is inside the 120s cooldown, so the condition is sticky.
"""

from __future__ import annotations

import fcntl
import json
import re
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class TestEveryTimerDrivenScanIsWatched:
    def test_every_script_service_is_in_the_scheduled_catalog(self):
        import data_refresh
        from watchdog.services import SCHEDULED_SERVICES

        missing = sorted(set(data_refresh._SCRIPT_SERVICES.values()) - set(SCHEDULED_SERVICES))
        assert not missing, (
            "the 15-minute RTH driver runs these, and check.py evaluates a "
            f"service only if it is in this catalog: {missing}"
        )

    def test_gex_scan_carries_vcg_scans_windows(self):
        from watchdog.services import SCHEDULED_SERVICES

        assert SCHEDULED_SERVICES["gex-scan"]["open"] == SCHEDULED_SERVICES["vcg-scan"]["open"]

    def test_the_web_catalog_no_longer_calls_it_on_demand(self):
        windows = (REPO / "web" / "lib" / "serviceHealthWindows.ts").read_text(encoding="utf-8")
        entry = next(ln for ln in windows.splitlines() if ln.strip().startswith('"gex-scan":'))
        assert '"scheduled"' in entry, entry


class TestOneCriTimeoutConstant:
    def test_the_route_and_the_timer_share_it(self):
        import data_refresh

        server = (SCRIPTS / "api" / "server.py").read_text(encoding="utf-8")
        body = "\n".join(
            ln for ln in server.splitlines() if not ln.lstrip().startswith("#")
        )
        call = next(ln for ln in body.splitlines() if 'run_script("cri_scan.py"' in ln)
        assert "CRI_SCAN_TIMEOUT_SECS" in call, (
            "the browser path SIGKILLs exactly the slow-IB runs the timer "
            f"budget was raised to accommodate: {call.strip()}"
        )
        assert data_refresh.CRI_SCAN_TIMEOUT_SECS == 180



# A blocking flock would park the loser behind a 180s run; a bounded join turns
# that hang into a failure instead of a stuck test session.
LOCK_LOSER_BUDGET_S = 15.0
CACHED_CRI = {"date": "2026-08-28", "cri": 42.0, "level": "T-314 cached payload"}


class _IBNeverConstructed:
    """Stands in for `ib_insync.IB`: the lock loser must not open a socket."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("the lock loser constructed an IB client")


@pytest.fixture
def cri_scan_sandbox(monkeypatch, tmp_path):
    """`cri_scan` pointed at a throwaway project dir, with IB and the
    off-hours cache gate stubbed so ONLY the advisory lock decides the path."""
    import ib_insync

    import cri_scan
    from utils import scan_cache_gate

    monkeypatch.setattr(cri_scan, "_PROJECT_DIR", tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(ib_insync, "IB", _IBNeverConstructed)
    monkeypatch.setattr(cri_scan, "fetch_all", _IBNeverConstructed)
    monkeypatch.setattr(scan_cache_gate, "cached_scan_if_fresh", lambda *a, **k: None)
    monkeypatch.setenv("RADON_CRI_SCAN_LOCK", str(tmp_path / "held.lock"))
    return cri_scan


@pytest.fixture
def held_scan_lock(tmp_path):
    """The test process holds the lock the way an in-flight timer run would."""
    with open(tmp_path / "held.lock", "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield handle


def _run_cri_main(monkeypatch, cri_scan, *argv) -> int:
    """`cri_scan.main()` under a wall-clock budget; its exit code."""
    monkeypatch.setattr(sys, "argv", ["cri_scan.py", *argv])
    outcome: dict = {}

    def target() -> None:
        try:
            cri_scan.main()
            outcome["exit"] = 0
        except SystemExit as exc:
            outcome["exit"] = exc.code
        except BaseException as exc:  # noqa: BLE001 — re-raised on the test thread
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(LOCK_LOSER_BUDGET_S)
    assert not worker.is_alive(), (
        "the advisory lock BLOCKED: a loser must serve cache or exit 75, "
        "not queue behind a run that may take 180s"
    )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["exit"]


class TestCriScanLockLoserServesCacheOrYields:
    def test_a_lock_loser_prints_the_cached_payload_and_touches_no_ib(
        self, monkeypatch, tmp_path, capsys, cri_scan_sandbox, held_scan_lock
    ):
        (tmp_path / "data" / "cri.json").write_text(json.dumps(CACHED_CRI))

        exit_code = _run_cri_main(monkeypatch, cri_scan_sandbox, "--json")

        assert exit_code == 0
        assert json.loads(capsys.readouterr().out) == CACHED_CRI

    def test_a_lock_loser_with_no_cache_exits_75(
        self, monkeypatch, tmp_path, capsys, cri_scan_sandbox, held_scan_lock
    ):
        assert not (tmp_path / "data" / "cri.json").exists()

        exit_code = _run_cri_main(monkeypatch, cri_scan_sandbox, "--json")

        assert exit_code == 75
        assert capsys.readouterr().out == ""


def _container_data_bind(runtime_script: str) -> tuple[Path, Path]:
    """(host DATA_DIR, container mount point) from radon-app-runtime.sh."""
    host = re.search(r"^\s*DATA_DIR=(\S+)$", runtime_script, re.M).group(1)
    mount = re.search(r'-v "\$\{DATA_DIR\}:(\S+)"', runtime_script).group(1)
    return Path(host), Path(mount)


def _container_workdir(runtime_script: str) -> Path:
    return Path(re.search(r"^\s*workdir=(\S+)$", runtime_script, re.M).group(1))


def _host_timer_workdir(unit: str) -> Path:
    return Path(re.search(r"^WorkingDirectory=(\S+)$", unit, re.M).group(1))


class TestCriScanLockIsSharedAcrossTheContainerBoundary:
    """The timer runs `cri_scan` on the HOST (`radon-refresh.service`); the
    browser's `/regime/scan` runs it INSIDE the app container. `/tmp` is a
    different inode on each side, so a `/tmp` lock serialises nothing across
    them. The lock must live under the one directory both sides share."""

    def test_the_default_lock_path_lands_inside_the_shared_data_bind(self, monkeypatch):
        import cri_scan

        monkeypatch.delenv("RADON_CRI_SCAN_LOCK", raising=False)
        runtime = (REPO / "cloud" / "scripts" / "radon-app-runtime.sh").read_text(encoding="utf-8")
        refresh_unit = (REPO / "cloud" / "services" / "radon-refresh.service").read_text(encoding="utf-8")
        host_data_dir, container_data_dir = _container_data_bind(runtime)

        lock = Path(cri_scan.scan_lock_path())
        assert lock.is_relative_to(cri_scan._PROJECT_DIR), (
            f"{lock} is outside the project tree, so it is outside every bind "
            "radon-app-runtime.sh hands the container"
        )
        in_project = lock.relative_to(cri_scan._PROJECT_DIR)

        on_host = _host_timer_workdir(refresh_unit) / in_project
        in_container = _container_workdir(runtime) / in_project
        assert on_host.is_relative_to(host_data_dir), on_host
        assert in_container.is_relative_to(container_data_dir), in_container

    def test_the_env_override_still_wins(self, monkeypatch, tmp_path):
        import cri_scan

        monkeypatch.setenv("RADON_CRI_SCAN_LOCK", str(tmp_path / "elsewhere.lock"))
        assert Path(cri_scan.scan_lock_path()) == tmp_path / "elsewhere.lock"


class TestGateSaturationIsVisible:
    def test_a_novel_subject_on_a_saturated_map_records_saturation(self, monkeypatch):
        import importlib

        from api.scan_gate import ScanGate

        server = importlib.import_module("api.server")
        emitted: list = []
        monkeypatch.setattr(server, "_write_scan_gate_saturation_row", lambda detail: emitted.append(detail))
        monkeypatch.setattr(server, "_SCAN_GATE_SATURATION_REPORTED_AT", None, raising=False)
        server._reset_scan_gates()
        try:
            for i in range(server.MAX_SUBJECT_SCAN_GATES):
                armed = ScanGate(f"cri:S{i}")
                armed.mark_failure()
                server._SUBJECT_SCAN_GATES[("cri", f"S{i}")] = armed

            gate = server._scan_gate_for("x", "new")

            assert gate is server._OVERFLOW_SCAN_GATE
            assert ("x", "NEW") not in server._SUBJECT_SCAN_GATES
            assert len(emitted) == 1, (
                "every tracked subject is backing off, nothing is evictable, and "
                f"the refusal left no trace: {emitted}"
            )
        finally:
            server._reset_scan_gates()

    def test_the_saturation_detail_names_the_cause(self):
        import importlib

        server = importlib.import_module("api.server")
        detail = server._scan_gate_overflow_detail()
        assert "saturat" in detail.lower(), detail
        assert str(server.MAX_SUBJECT_SCAN_GATES) in detail, detail

    def test_the_429_detail_names_saturation_not_a_scan_failure(self):
        import importlib

        server = importlib.import_module("api.server")
        assert "saturat" in server._scan_gate_overflow_detail().lower()

    def test_a_burst_emits_one_record_not_one_per_request(self, monkeypatch):
        import importlib

        server = importlib.import_module("api.server")
        emitted: list = []
        monkeypatch.setattr(server, "_write_scan_gate_saturation_row", lambda detail: emitted.append(detail))
        monkeypatch.setattr(server, "_SCAN_GATE_SATURATION_REPORTED_AT", None, raising=False)
        for _ in range(20):
            server._record_scan_gate_saturation()
        assert len(emitted) == 1, emitted

    def test_the_first_burst_after_a_reboot_is_still_reported(self, monkeypatch):
        """`time.monotonic()` counts host UPTIME, so a `0.0` sentinel does not
        mean "never reported" — it means "reported at boot". On a host less
        than 300s old the whole first burst was swallowed. Green on any
        long-lived machine, red on a fresh CI runner."""
        import importlib

        server = importlib.import_module("api.server")
        emitted: list = []
        monkeypatch.setattr(server, "_write_scan_gate_saturation_row", lambda detail: emitted.append(detail))
        monkeypatch.setattr(server, "_SCAN_GATE_SATURATION_REPORTED_AT", None, raising=False)
        monkeypatch.setattr(server.time, "monotonic", lambda: 90.0)
        for _ in range(20):
            server._record_scan_gate_saturation()
        assert len(emitted) == 1, emitted
