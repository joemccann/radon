"""The Mac mini dispatcher keeps the Tier-3 prober near its five-minute cadence.

GitHub's ``*/5`` cron is best-effort. By 2026-09-26 the scheduled runs of
``external-health-probe.yml`` were 2-5 hours apart, past the two-hour
dead-man window shared by ``scripts/health_probe/reader.py`` and
``web/lib/adminReliability.ts``, so the admin tile sat on "Stale" and the
watchdog's witness had nothing fresh to read. The always-on runner asks for
the run instead; the probe's identity, code path and Turso row are unchanged.
"""

from __future__ import annotations

import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from health_probe import reader

REPO = Path(__file__).resolve().parents[2]
PLIST = REPO / "config" / "com.radon.external-probe-dispatch.plist"
SETUP = REPO / "scripts" / "setup_external_probe_dispatch.sh"
WORKFLOW = REPO / ".github" / "workflows" / "external-health-probe.yml"
BASH = shutil.which("bash") or "/bin/bash"

WORKFLOW_FILE = "external-health-probe.yml"
DISPATCH_INTERVAL_SECONDS = 300


def _plist() -> dict:
    with PLIST.open("rb") as fh:
        return plistlib.load(fh)


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _cron_minutes(field: str) -> set[int]:
    minutes: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part == "*":
            start, stop = 0, 59
        elif "-" in part:
            start_text, stop_text = part.split("-", 1)
            start, stop = int(start_text), int(stop_text)
        else:
            start = stop = int(part)
        minutes.update(range(start, stop + 1, step))
    return minutes


class TestDispatcherPlist:
    def test_label_matches_the_filename(self):
        assert _plist()["Label"] == PLIST.name.removesuffix(".plist")

    def test_dispatches_the_probe_workflow_on_main(self):
        command = " ".join(_plist()["ProgramArguments"])
        assert f"gh workflow run {WORKFLOW_FILE} -R joemccann/radon --ref main" in command
        assert (REPO / ".github" / "workflows" / WORKFLOW_FILE).is_file()

    def test_fires_every_five_minutes_well_inside_the_dead_man_window(self):
        interval = _plist()["StartInterval"]
        assert interval == DISPATCH_INTERVAL_SECONDS
        # Several consecutive missed fires still leave the row fresh.
        assert interval * 4 <= reader.STALE_AFTER_SECONDS

    def test_logs_under_the_runner_log_directory(self):
        plist = _plist()
        for key in ("StandardOutPath", "StandardErrorPath"):
            assert plist[key].startswith("__HOME__/radon-weekend/logs/"), key
        assert plist["EnvironmentVariables"]["HOME"] == "__HOME__"


class TestWorkflowAcceptsDispatch:
    def test_workflow_dispatch_trigger_is_declared(self):
        assert re.search(r"^\s+workflow_dispatch:", _workflow_text(), re.M)

    def test_a_dispatch_never_cancels_an_in_flight_probe(self):
        concurrency = yaml.safe_load(_workflow_text())["concurrency"]
        assert concurrency["cancel-in-progress"] is False

    def test_cron_skips_the_top_of_the_hour(self):
        # GitHub documents the start of every hour as its high-load window
        # for scheduled workflows; the fallback cron stays off that slot.
        cron = re.search(r'cron:\s*"([^"]+)"', _workflow_text()).group(1)
        minutes = _cron_minutes(cron.split()[0])
        assert 0 not in minutes
        assert len(minutes) == 12


class TestSetupScript:
    def test_parses(self):
        assert subprocess.run([BASH, "-n", str(SETUP)], capture_output=True).returncode == 0

    def test_installs_this_plist_with_the_home_substitution(self):
        text = SETUP.read_text(encoding="utf-8")
        assert PLIST.name in text
        assert "__HOME__" in text
        assert "launchctl bootstrap" in text
