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

import re
import sys
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

    def test_cri_scan_serialises_concurrent_runs(self):
        source = (SCRIPTS / "cri_scan.py").read_text(encoding="utf-8")
        body = "\n".join(ln for ln in source.splitlines() if not ln.lstrip().startswith("#"))
        assert "fcntl" in body or "flock" in body, (
            "a browser POST landing during a timer fire runs a second cri_scan "
            "concurrently, racing the same data/cri.json and IB client-id range"
        )

    def test_the_advisory_lock_is_non_blocking(self):
        """A second caller must serve cache, not queue behind a 180s run."""
        source = (SCRIPTS / "cri_scan.py").read_text(encoding="utf-8")
        assert "LOCK_NB" in source, source[:0] or "expected a non-blocking flock"


class TestGateSaturationIsVisible:
    def test_the_overflow_branch_records_saturation(self):
        server = (SCRIPTS / "api" / "server.py").read_text(encoding="utf-8")
        body = "\n".join(ln for ln in server.splitlines() if not ln.lstrip().startswith("#"))
        start = body.index("def _scan_gate_for(")
        end = body.index("\ndef ", start + 1)
        branch = body[start:end]
        assert "_record_scan_gate_saturation" in branch, (
            "the only externally visible artifact of saturation is a 429 whose "
            "detail says 'backing off after a failure'"
        )

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
