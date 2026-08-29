"""R-391 / R-392 / REL-138: the drop-ins keep the supervision and the drift signal.

R-391 (SUPERSEDED 2026-08-29, see below): base `radon-relay.service` is
`Type=notify` + `WatchdogSec=45`; base `radon-monitor.service` is
`Type=notify` + `WatchdogSec=900`, the latter installed BY REL-008 to catch the
alive-but-dead loop wedge. R-391 made both drop-ins inherit that contract
instead of overriding it to `Type=simple` + `WatchdogSec=infinity`.

Those drop-ins could not be installed until the privileged gate was fixed, so
R-391 first reached production on 2026-08-29 -- and both units immediately
restart-looped. systemd attributes a notify datagram to a unit by the SENDER'S
CGROUP. `radon-app-runtime.sh` runs the payload under
`--cgroup-parent=system.slice`, so the container lands in
`/system.slice/docker-<id>.scope`, never in `/system.slice/<unit>`; the
datagram is unattributable, `Type=notify` hangs at `activating` until
TimeoutStartSec, and the unit restart-loops.

Measured, not inferred:

  * the app side is correct -- running `radon-node` with `NOTIFY_SOCKET` set
    delivers `READY=1` (`ib_realtime_server.js:383`), and `socat` in the image
    reaches a host socket
  * the placement cannot be fixed: docker's systemd cgroup driver rejects
    `--cgroup-parent=system.slice/<unit>.service` with "cgroup-parent for
    systemd cgroup should be a valid slice named as xxx.slice"

So a CONTAINERISED unit cannot satisfy `Type=notify`, and these tests now pin
that constraint rather than the contract it makes impossible. The supervision
R-391 restored is genuinely lost and needs a different mechanism; it is not
re-established by the drop-in.

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


class TestContainerisedUnitsCannotUseNotify:
    """The 2026-08-29 production regression, pinned as a constraint.

    A drop-in that asks systemd to wait for `READY=1` from a process systemd
    cannot attribute to the unit is not stricter supervision -- it is a
    guaranteed restart loop. Every one of these units runs its payload in a
    container placed outside the unit cgroup.
    """

    @pytest.mark.parametrize("unit", APP_UNITS)
    def test_no_containerised_dropin_asks_for_notify(self, unit):
        drop_type = _directive(_dropin(unit), "Type") or "simple"
        assert drop_type != "notify", (
            f"{unit}: the payload runs under --cgroup-parent=system.slice, so its "
            "READY=1 is unattributable and systemd hangs at `activating` until "
            "TimeoutStartSec, then restart-loops (observed in production "
            "2026-08-29). Docker refuses --cgroup-parent=system.slice/<unit>."
        )

    @pytest.mark.parametrize("unit", APP_UNITS)
    def test_a_notify_dropin_would_have_to_neutralise_the_watchdog_too(self, unit):
        """`WatchdogSec` without a reachable notify socket is a timed SIGABRT."""
        if (_directive(_base(unit), "WatchdogSec")) is None:
            return
        assert _directive(_dropin(unit), "WatchdogSec") == "infinity", (
            f"{unit}: the base declares a watchdog the container can never feed, "
            "so the drop-in must disable it explicitly rather than inherit it."
        )

    def test_the_base_units_keep_their_watchdogs_for_a_host_run(self):
        """Unchanged by this: the base units are still correct off-container."""
        assert _directive(_base("radon-monitor.service"), "WatchdogSec") == "900"
        assert _directive(_base("radon-relay.service"), "WatchdogSec") == "45"

    def test_the_dropin_states_why_supervision_is_downgraded(self):
        """A bare `Type=simple` reads as an oversight; this one is a finding."""
        for unit in ("radon-monitor.service", "radon-relay.service"):
            body = _dropin(unit)
            assert "cgroup" in body.lower(), unit
            assert "2026-08-29" in body, unit

    def test_the_notify_plumbing_is_still_forwarded_for_a_future_fix(self):
        """Harmless while unused, and the only thing a real fix would reuse."""
        source = RUNTIME.read_text(encoding="utf-8")
        assert "WATCHDOG_USEC" in source
        assert "NOTIFY_SOCKET" in source


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
