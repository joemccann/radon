"""R-391 / R-392 / REL-138: the drop-ins keep the supervision and the drift signal.

R-391: base `radon-relay.service` is `Type=notify` + `WatchdogSec=45`; base
`radon-monitor.service` is `Type=notify` + `WatchdogSec=900`, the latter
installed BY REL-008 to catch the alive-but-dead loop wedge. Both drop-ins
overrode to `Type=simple` + `WatchdogSec=infinity`, so systemd stopped waiting
for `READY=1` and stopped requiring keepalives -- reverting REL-008 and DUR-17
and making the `NOTIFY_SOCKET` / `WATCHDOG_USEC` plumbing in
`radon-app-runtime.sh` dead code.

R-392: `_check_units` merges the LIVE unit with its drop-ins but compares
against the repo BASE unit alone, so every setting a drop-in adds reads as
live-only. All five app units go permanently `unit-mismatch` the moment the
drop-ins are installed, and a permanently-red `config-drift` buries every real
drift. Resolving it with an allowlist entry would be wrong: the R-058 ratchet is
a bounded acknowledgment, not a suppression for a permanent, intended state.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICES = CLOUD_ROOT / "services"
RUNTIME = CLOUD_ROOT / "scripts" / "radon-app-runtime.sh"

sys.path.insert(0, str(CLOUD_ROOT / "scripts"))

APP_UNITS = (
    "radon-api.service",
    "radon-monitor.service",
    "radon-relay.service",
    "radon-nextjs.service",
    "radon-newsfeed.service",
)


def _directive(text: str, key: str) -> str | None:
    value = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            value = stripped.split("=", 1)[1].strip()
    return value


def _base(unit: str) -> str:
    return (SERVICES / unit).read_text(encoding="utf-8")


def _dropin(unit: str) -> str:
    return (SERVICES / f"{unit}.d" / "runtime-container.conf").read_text(encoding="utf-8")


class TestSupervisionSurvivesContainerisation:
    @pytest.mark.parametrize("unit", APP_UNITS)
    def test_the_dropin_does_not_downgrade_the_service_type(self, unit):
        base_type = _directive(_base(unit), "Type") or "simple"
        drop_type = _directive(_dropin(unit), "Type")
        assert drop_type == base_type, (
            f"{unit}: base is Type={base_type} but the drop-in forces "
            f"Type={drop_type}; systemd stops waiting for READY=1"
        )

    @pytest.mark.parametrize("unit", APP_UNITS)
    def test_the_dropin_does_not_disable_the_watchdog(self, unit):
        base_wd = _directive(_base(unit), "WatchdogSec")
        drop_wd = _directive(_dropin(unit), "WatchdogSec")
        if base_wd is None:
            return  # the base never had one; nothing to preserve
        assert drop_wd in (None, base_wd), (
            f"{unit}: base WatchdogSec={base_wd}, drop-in={drop_wd}. A relay "
            "that stops delivering ticks, or a monitor wedged on an IB socket, "
            "used to get SIGABRT and a restart; with infinity it sits "
            "active (running) forever with a dead socket."
        )

    def test_the_two_notify_units_keep_their_watchdogs(self):
        """The specific regression: REL-008 (monitor 900s) and DUR-17 (relay 45s)."""
        assert _directive(_base("radon-monitor.service"), "WatchdogSec") == "900"
        assert _directive(_base("radon-relay.service"), "WatchdogSec") == "45"
        for unit in ("radon-monitor.service", "radon-relay.service"):
            # Strip comments first: the explanatory comment QUOTES the value it
            # is explaining, and would satisfy or break this assertion by itself.
            body = "\n".join(
                ln for ln in _dropin(unit).splitlines() if not ln.lstrip().startswith("#")
            )
            assert "infinity" not in body, unit

    def test_the_notify_socket_plumbing_is_reachable(self):
        """`WATCHDOG_USEC` is only set for a Type=notify unit with a watchdog."""
        source = RUNTIME.read_text(encoding="utf-8")
        assert "WATCHDOG_USEC" in source
        assert "NOTIFY_SOCKET" in source
        notify_units = [
            u for u in APP_UNITS if (_directive(_dropin(u), "Type") or "simple") == "notify"
        ]
        assert set(notify_units) == {"radon-monitor.service", "radon-relay.service"}, notify_units


class TestDriftAuditSeesTheRepoDropins:
    def _fixture(self, tmp_path, mutate: str | None):
        import drift_audit

        repo = tmp_path / "repo"
        live = tmp_path / "live"
        (repo / "services" / "radon-api.service.d").mkdir(parents=True)
        live.mkdir()
        base = "[Unit]\nDescription=x\n[Service]\nUser=radon\nExecStart=/bin/true\n"
        dropin = "[Service]\nUser=root\nType=simple\nExecStartPre=\nExecStart=\nExecStart=/bin/false\n"
        (repo / "services" / "radon-api.service").write_text(base, encoding="utf-8")
        (repo / "services" / "radon-api.service.d" / "runtime-container.conf").write_text(
            dropin, encoding="utf-8"
        )
        (live / "radon-api.service").write_text(base, encoding="utf-8")
        (live / "radon-api.service.d").mkdir()
        installed = dropin if mutate is None else dropin + mutate
        (live / "radon-api.service.d" / "runtime-container.conf").write_text(
            installed, encoding="utf-8"
        )
        return drift_audit, repo, live

    def _drifts(self, monkeypatch, tmp_path, mutate: str | None):
        drift_audit, repo, live = self._fixture(tmp_path, mutate)
        monkeypatch.setattr(drift_audit, "REPO", repo)
        monkeypatch.setattr(drift_audit, "SYSTEMD_DIR", live)
        monkeypatch.setattr(
            drift_audit, "_read_repo", lambda rel: (repo / rel).read_text(encoding="utf-8")
            if (repo / rel).is_file() else None
        )
        drifts: list[dict] = []
        drift_audit._check_units(drifts, [])
        return drifts

    def test_an_installed_dropin_that_matches_the_repo_is_not_drift(self, monkeypatch, tmp_path):
        drifts = self._drifts(monkeypatch, tmp_path, None)
        assert not [d for d in drifts if d["id"].startswith("unit-mismatch")], drifts

    def test_a_mutated_installed_dropin_still_reports_drift(self, monkeypatch, tmp_path):
        drifts = self._drifts(monkeypatch, tmp_path, "Environment=EVIL=1\n")
        assert [d for d in drifts if d["id"] == "unit-mismatch:radon-api.service"], drifts

    def test_the_fix_is_not_an_allowlist_entry(self):
        allowlist = (CLOUD_ROOT / "config" / "drift-allowlist.conf").read_text(encoding="utf-8")
        offenders = [
            ln.strip() for ln in allowlist.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
            and re.search(r"unit-mismatch:radon-(api|monitor|relay|nextjs|newsfeed)\.service", ln)
        ]
        assert not offenders, (
            "the R-058 ratchet is a bounded acknowledgment, not a suppression "
            f"for a permanent intended state: {offenders}"
        )
