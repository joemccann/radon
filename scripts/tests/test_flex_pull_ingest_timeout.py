"""2026-09-15: radon-flex-pull Result=timeout after TimeoutStartSec=120.

Page 68388b70… 11:35Z. ExecMainStart 11:30:01Z → InactiveEnter 11:32:01Z
(exactly 120s), NRestarts=0, ExecMainStatus=15. IBKR outgoing held 24
accumulated .pgp files (12 Equity_Summary + 12 Trade_History); alphabetical
oldest-first walked duplicates then two NEW Equity_Summary statements, each
running perf_twr (~60s). systemd SIGTERM'd mid-second ingest; no flex-pull
heartbeat (row still 2026-09-12). Edge and :8321/health/lite stayed up.

SFTP_TIMEOUT_SECS=45 only bounds the OpenSSH child. The unit budget must
cover a multi-statement morning catch-up, and the process must self-limit
with a heartbeat before TimeoutStartSec kills it.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flex_sftp_pull as pull  # noqa: E402
from test_flex_sftp_pull import AFTER_FIRST_DELIVERY, FakeSftp, _ssh_config  # noqa: E402
from test_rel146_flex_sftp_honesty import _config_lines, _write  # noqa: E402

ZONE = "America/New_York"
UNIT = (
    Path(__file__).resolve().parents[2]
    / "cloud"
    / "services"
    / "radon-flex-pull.service"
)


def _unit_timeout_sec() -> int:
    line = next(
        ln for ln in UNIT.read_text().splitlines() if ln.startswith("TimeoutStartSec=")
    )
    return int(line.split("=", 1)[1])


def _drive_many(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    n_files: int,
    ingest_sleep: float,
    budget_s: float,
    newest_outcome: str = "applied",
) -> tuple[int, list[tuple], list[str]]:
    beats: list[tuple] = []
    seen: list[str] = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
    monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
    monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "activity")
    monkeypatch.setattr(pull, "SWEEP_BUDGET_S", budget_s, raising=False)

    files = {
        f"U4698258.Equity_Summary_in_Base.202609{day:02d}.202609{day:02d}.xml.pgp": b"<x/>"
        for day in range(1, n_files + 1)
    }

    def ingest(xml_text, source_path="", **k):
        name = Path(source_path).name if source_path else ""
        seen.append(name)
        time.sleep(ingest_sleep)
        # Newest calendar day is the only fresh statement.
        if "202609%02d" % n_files in name:
            return {"ok": True, "outcome": newest_outcome}
        return {"ok": True, "outcome": "duplicate"}

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    code = pull.run(
        config=_write(tmp_path, _config_lines()),
        inbox=inbox,
        runner=FakeSftp(files),
        decrypt=lambda data, **k: data.decode(),
        ingest=ingest,
        now=AFTER_FIRST_DELIVERY,
    )
    return code, beats, seen


class TestSweepBudget:
    def test_tarpitted_ingest_stops_inside_the_wall_clock_budget(self, tmp_path, monkeypatch):
        """Reproduce 2026-09-15: many files + slow ingest must not reach systemd."""
        assert hasattr(pull, "SWEEP_BUDGET_S"), "process must own a wall-clock budget"
        code, beats, seen = _drive_many(
            tmp_path,
            monkeypatch,
            n_files=8,
            ingest_sleep=0.15,
            budget_s=0.35,
            newest_outcome="applied",
        )
        assert code in (0, 1)
        assert beats, "budget stop must heartbeat; bare timeout left no row"
        assert len(seen) < 8, f"walked all files under a tiny budget: {seen}"
        # Budget path must not look like a clean full-batch ok with no note.
        final_state, final_err = beats[-1]
        if final_state == "ok":
            assert final_err is not None

    def test_newest_statements_are_attempted_before_older_duplicates(
        self, tmp_path, monkeypatch
    ):
        """Alphabetical oldest-first burned the 120s budget on duplicates first."""
        code, beats, seen = _drive_many(
            tmp_path,
            monkeypatch,
            n_files=6,
            ingest_sleep=0.12,
            budget_s=0.30,
            newest_outcome="applied",
        )
        assert seen, "expected at least one ingest attempt"
        assert "20260906" in seen[0], f"newest-first required, got order {seen}"

    def test_sweep_budget_fits_inside_unit_start_timeout(self):
        assert hasattr(pull, "SWEEP_BUDGET_S")
        assert hasattr(pull, "INGEST_HEADROOM_S")
        unit_timeout = _unit_timeout_sec()
        assert pull.SWEEP_BUDGET_S + pull.INGEST_HEADROOM_S <= unit_timeout
        # 07:30 → 08:30 ET gap is 3600s; keep well under so the retry can fire.
        assert unit_timeout < 3600

    def test_unit_start_timeout_covers_multi_statement_catchup(self):
        """Tue catch-up: 24 remote files + two ~60s perf_twr ingests measured
        past 120s on 2026-09-15. Floor must clear that healthy path."""
        assert _unit_timeout_sec() >= 900


class TestSigtermHeartbeat:
    def test_sigterm_unwind_is_installed_and_heartbeats(self, tmp_path, monkeypatch):
        """TimeoutStartSec SIGTERM skipped every handler; pin the install site."""
        assert hasattr(pull, "install_sigterm_unwind")
        beats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))

        installed: dict[int, object] = {}
        real_signal = signal.signal

        def spy(sig, handler):
            installed[sig] = handler
            return real_signal(sig, handler)

        monkeypatch.setattr(signal, "signal", spy)
        pull.install_sigterm_unwind()
        assert signal.SIGTERM in installed

        # Drive run() so the call site is pinned (deleting main/run install reds).
        monkeypatch.setattr(pull, "install_sigterm_unwind", pull.install_sigterm_unwind)
        seen_install = {"n": 0}
        real_install = pull.install_sigterm_unwind

        def counting_install():
            seen_install["n"] += 1
            return real_install()

        monkeypatch.setattr(pull, "install_sigterm_unwind", counting_install)

        def _runner(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 1))

        # Clear and re-spy heartbeat through run's outer guarantee.
        beats.clear()
        code = pull.run(
            config=_write(tmp_path, _config_lines()),
            inbox=tmp_path / "inbox",
            runner=_runner,
            now=AFTER_FIRST_DELIVERY,
        )
        assert seen_install["n"] >= 1, "run() must install the SIGTERM unwind"
        assert code == 1
        assert beats and beats[-1][0] == "error"


class TestClaimStaleTracksStartTimeout:
    def test_stale_window_nests_between_start_timeout_and_timer_gap(self):
        from db import writer

        unit_timeout = _unit_timeout_sec()
        assert unit_timeout < writer.FLEX_CLAIM_STALE_AFTER_S < 3600
