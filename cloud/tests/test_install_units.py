"""`radon-deploy-root install-units` — the CI deploy installs the timer-owned
units recorded in config/installed-units.sha256, replacing the operator's
`ssh root` + `install -m 0644 ... && systemctl daemon-reload && enable --now`.

The manifest digest is the review gate: a unit whose checkout content does
not hash to its manifest entry is NOT installed (that is exactly the
pending-install window the drift allowlist covers). Control-plane units stay
bootstrap-owned; gateway units stay excluded; `enable --now` is issued for
NEWLY installed timers only and never for a timer-owned .service (enabling
both made every scheduled job fire at once, setup-vps.sh:590-593).
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shlex
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOT_HELPER = ROOT / "scripts" / "deploy-root-helper.sh"

SERVICE_BODY = "[Unit]\nDescription=x\n[Service]\nType=oneshot\nExecStart=/bin/true\n"
TIMER_BODY = "[Unit]\nDescription=x\n[Timer]\nOnCalendar=daily\n[Install]\nWantedBy=timers.target\n"


def _write_executable(path: pathlib.Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    )


class Sandbox:
    """The checkout is a real git repo that doubles as its own `main` remote.

    `install-units` sources both the manifest and every unit body from git
    objects at the GitHub main tip, so the fixture has to commit before the
    helper can see anything -- exactly the property under test.
    """

    def __init__(self, tmp_path: pathlib.Path):
        self.tmp = tmp_path
        self.repo = tmp_path / "checkout"
        self.services = self.repo / "cloud" / "services"
        self.services.mkdir(parents=True)
        self.manifest = self.repo / "cloud" / "config" / "installed-units.sha256"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text("#\n", encoding="utf-8")
        _git(self.repo, "init", "-b", "main", ".")
        _git(self.repo, "config", "user.email", "install-units@test")
        _git(self.repo, "config", "user.name", "install-units")
        self.unit_dir = tmp_path / "etc" / "systemd" / "system"
        self.unit_dir.mkdir(parents=True)
        self.systemctl_log = tmp_path / "systemctl.log"
        fake_systemctl = tmp_path / "systemctl"
        _write_executable(
            fake_systemctl,
            f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(self.systemctl_log))}\nexit 0\n",
        )
        fake_sync = tmp_path / "sync"
        _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
        fake_rm = tmp_path / "rm"
        _write_executable(fake_rm, '#!/bin/bash\nexec /bin/rm "$@"\n')
        # macOS has no sha256sum; mirror its `<digest>  <path>` output.
        sha256sum = tmp_path / "sha256sum"
        _write_executable(
            sha256sum,
            '#!/bin/bash\nif command -v sha256sum >/dev/null 2>&1; then exec sha256sum "$@"; fi\n'
            'exec shasum -a 256 "$@"\n',
        )
        self.env = {
            **os.environ,
            "RADON_DEPLOY_HELPER_TEST_MODE": "1",
            "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
            "RADON_TEST_RM": str(fake_rm),
            "RADON_TEST_SYNC": str(fake_sync),
            "RADON_TEST_ACTIVE_STATE_FILE": str(tmp_path / "active-units"),
            "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
            "RADON_TEST_GIT_DIR": str(self.repo / ".git"),
            "RADON_TEST_UNIT_REMOTE": str(self.repo),
            "RADON_TEST_SYSTEMD_UNIT_DIR": str(self.unit_dir),
            "RADON_TEST_SHA256SUM": str(sha256sum),
        }

    def source(self, name: str, body: str, *, record: bool = True) -> pathlib.Path:
        path = self.services / name
        path.write_text(body, encoding="utf-8")
        if record:
            self.record(name, body)
        return path

    def record(self, name: str, body: str) -> None:
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(f"{_sha(body)}  {name}\n")

    def installed(self, name: str, body: str) -> pathlib.Path:
        path = self.unit_dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def commit(self) -> str:
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "--allow-empty", "-m", "release")
        return subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()

    def run(self, *, commit: bool = True) -> subprocess.CompletedProcess[str]:
        if commit:
            self.commit()
        return subprocess.run(
            ["bash", str(ROOT_HELPER), "install-units"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def systemctl_calls(self) -> list[str]:
        if not self.systemctl_log.exists():
            return []
        return self.systemctl_log.read_text(encoding="utf-8").splitlines()


def test_new_timer_pair_is_installed_reloaded_and_only_the_timer_enabled(tmp_path):
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.service", SERVICE_BODY)
    box.source("radon-credit-spread.timer", TIMER_BODY)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.unit_dir / "radon-credit-spread.service").read_text() == SERVICE_BODY
    assert (box.unit_dir / "radon-credit-spread.timer").read_text() == TIMER_BODY
    assert (box.unit_dir / "radon-credit-spread.timer").stat().st_mode & 0o777 == 0o644
    calls = box.systemctl_calls()
    assert calls.count("daemon-reload") == 1
    assert "enable --now radon-credit-spread.timer" in calls
    assert not any("radon-credit-spread.service" in c for c in calls), calls
    assert "installed=2" in result.stdout


def test_changed_unit_is_refreshed_without_re_enabling(tmp_path):
    box = Sandbox(tmp_path)
    new_timer = TIMER_BODY.replace("daily", "*-*-* 21:45:00 UTC")
    box.source("radon-credit-spread.timer", new_timer)
    box.installed("radon-credit-spread.timer", TIMER_BODY)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.unit_dir / "radon-credit-spread.timer").read_text() == new_timer
    calls = box.systemctl_calls()
    assert calls == ["daemon-reload"]
    assert "updated=1" in result.stdout


def test_unchanged_units_touch_nothing(tmp_path):
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.timer", TIMER_BODY)
    box.installed("radon-credit-spread.timer", TIMER_BODY)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.systemctl_calls() == []
    assert "unchanged=1" in result.stdout


def test_manifest_mismatch_is_not_installed(tmp_path):
    """An edit in the checkout that nobody bumped in the manifest is
    unreviewed — the manifest digest is the review gate."""
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.timer", TIMER_BODY)  # manifest = original
    (box.services / "radon-credit-spread.timer").write_text(
        TIMER_BODY + "OnCalendar=*:0/5\n", encoding="utf-8"
    )
    box.installed("radon-credit-spread.timer", TIMER_BODY)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.unit_dir / "radon-credit-spread.timer").read_text() == TIMER_BODY
    assert box.systemctl_calls() == []
    assert "manifest-mismatch" in result.stdout


def test_control_plane_and_gateway_units_are_never_touched(tmp_path):
    box = Sandbox(tmp_path)
    for name in ("radon-api.service", "radon-ib-gateway.service", "radon-beta-x.service"):
        box.source(name, SERVICE_BODY)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert list(box.unit_dir.iterdir()) == []
    assert box.systemctl_calls() == []


def test_symlinked_source_is_refused(tmp_path):
    box = Sandbox(tmp_path)
    secret = tmp_path / "shadow"
    secret.write_text("root:$6$rootpasswordhash\n", encoding="utf-8")
    box.record("radon-credit-spread.timer", secret.read_text())
    (box.services / "radon-credit-spread.timer").symlink_to(secret)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (box.unit_dir / "radon-credit-spread.timer").exists()
    assert box.systemctl_calls() == []


def test_malformed_manifest_lines_are_ignored_and_symlinked_targets_skipped(tmp_path):
    box = Sandbox(tmp_path)
    with box.manifest.open("a", encoding="utf-8") as fh:
        fh.write("not-a-digest  radon-x.service\n")
        fh.write(f"{_sha('x')}  ../../etc/passwd\n")
    box.source("radon-credit-spread.timer", TIMER_BODY)
    elsewhere = tmp_path / "elsewhere.timer"
    elsewhere.write_text("old", encoding="utf-8")
    (box.unit_dir / "radon-credit-spread.timer").symlink_to(elsewhere)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert elsewhere.read_text() == "old"
    assert (box.unit_dir / "radon-credit-spread.timer").is_symlink()
    assert box.systemctl_calls() == []


def test_missing_manifest_is_a_hard_error(tmp_path):
    box = Sandbox(tmp_path)
    box.manifest.unlink()

    result = box.run()

    assert result.returncode == 66
    # The supervisor's job-cancel sweep may list jobs on a failed action;
    # nothing may be reloaded or enabled.
    assert not any(c.startswith(("daemon-reload", "enable")) for c in box.systemctl_calls())


DEPLOY = ROOT / "scripts" / "deploy.sh"


def _restart_services_shell(calls: pathlib.Path, *, install_rc: int = 0) -> str:
    return f"""
set -euo pipefail
source {DEPLOY!s}
prepare_release_transition() {{ return 0; }}
activate_staged_release() {{ printf 'activate\\n' >> {calls!s}; }}
wait_for_gateway_ready() {{ return 0; }}
sudo() {{
  printf '%s\\n' "$*" >> {calls!s}
  [[ "$*" == *install-units ]] && return {install_rc}
  return 0
}}
restart_services aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""


def _run_shell(shell: str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_DEPLOY_ROOT_HELPER": "/fixed/root-helper",
            "RADON_DIR": str(tmp_path / "live"),
            "RADON_CLOUD_DIR": str(ROOT),
        },
        capture_output=True,
        text=True,
    )


def test_deploy_installs_units_after_promote_and_before_restart(tmp_path):
    """The live checkout only becomes the target SHA at activation, so the
    install must follow it -- and precede restart-managed so new timers are
    live for the release that shipped them."""
    calls = tmp_path / "calls"
    result = _run_shell(_restart_services_shell(calls), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert "/fixed/root-helper stop-clean" in lines
    assert "activate" in lines
    assert "/fixed/root-helper install-units" in lines
    assert "/fixed/root-helper restart-managed" in lines
    refresh_idx = next(
        (i for i, line in enumerate(lines) if line.endswith("refresh-control-plane")),
        None,
    )
    assert refresh_idx is not None, lines
    assert lines.index("/fixed/root-helper stop-clean") < lines.index("activate")
    assert lines.index("activate") < lines.index("/fixed/root-helper install-units")
    assert lines.index("/fixed/root-helper install-units") < refresh_idx
    assert refresh_idx < lines.index("/fixed/root-helper restart-managed")


def test_deploy_unit_install_failure_is_non_fatal(tmp_path):
    calls = tmp_path / "calls"
    result = _run_shell(_restart_services_shell(calls, install_rc=66), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text(encoding="utf-8").splitlines()[-1] == "/fixed/root-helper restart-managed"
    assert "drift audit" in result.stdout + result.stderr



# --- REL-039 (R-084, R-091): the checkout is radon-writable, so it may not be
# the trust source. Unit content and the manifest that gates it both come from
# git objects at the GitHub main tip, and the control-plane exclusion covers
# the TIMERS that schedule the control-plane services.


def test_unit_absent_from_the_main_tip_is_refused(tmp_path):
    """R-084: anything running as `radon` can drop a unit into the checkout
    and append its digest to the checkout manifest. Neither is reachable from
    the pinned main tip, so neither may be installed."""
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.timer", TIMER_BODY)
    # The manifest line IS reviewed content at the tip; the unit body is not.
    # This is the strongest form of R-084: the digest gate passes and the blob
    # still has to be reachable from the tip.
    evil = "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/sh -c 'id > /tmp/pwn'\n"
    box.record("radon-evil.service", evil)
    box.commit()
    (box.services / "radon-evil.service").write_text(evil, encoding="utf-8")

    result = box.run(commit=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (box.unit_dir / "radon-evil.service").exists()
    assert not any("radon-evil" in c for c in box.systemctl_calls())
    assert "radon-evil.service (not-at-main-tip)" in result.stdout
    assert "installed=1" in result.stdout  # the reviewed timer still lands


def test_checkout_edit_after_the_tip_never_reaches_systemd(tmp_path):
    """The blob at the tip is the content installed, not the working tree."""
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.timer", TIMER_BODY)
    box.commit()
    (box.services / "radon-credit-spread.timer").write_text(
        TIMER_BODY.replace("OnCalendar=daily", "OnCalendar=*:0/1"), encoding="utf-8"
    )

    result = box.run(commit=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.unit_dir / "radon-credit-spread.timer").read_text() == TIMER_BODY


def test_head_behind_the_main_tip_refuses_the_whole_install(tmp_path):
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.timer", TIMER_BODY)
    box.commit()
    remote = tmp_path / "remote"
    subprocess.run(
        ["git", "clone", "--bare", str(box.repo), str(remote)],
        check=True,
        capture_output=True,
    )
    box.source("radon-credit-spread.service", SERVICE_BODY)
    box.commit()  # local HEAD now ahead of the "GitHub" tip
    box.env["RADON_TEST_UNIT_REMOTE"] = str(remote)

    result = box.run(commit=False)

    assert result.returncode == 76, result.stdout + result.stderr
    assert list(box.unit_dir.iterdir()) == []
    # The supervisor's job-cancel sweep may list jobs on a failed action;
    # nothing may be reloaded or enabled.
    assert not any(c.startswith(("daemon-reload", "enable")) for c in box.systemctl_calls())


def test_control_plane_timers_are_never_installed(tmp_path):
    """R-091: rewriting radon-drift-audit.timer to monthly hides an installed
    unit for 31 days; the same trick on radon-ib-watchdog.timer disarms
    gateway supervision. Both are control-plane, like their .service."""
    box = Sandbox(tmp_path)
    for name in (
        "radon-ib-watchdog.timer",
        "radon-portfolio-sync.timer",
        "radon-refresh.timer",
        "radon-db-backup.timer",
        "radon-drift-audit.timer",
        "radon-nextjs-db-watchdog.timer",
    ):
        box.source(name, TIMER_BODY)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert list(box.unit_dir.iterdir()) == []
    assert box.systemctl_calls() == []
    assert "(control-plane)" in result.stdout


def test_verify_control_plane_covers_the_scheduling_timers():
    """A control-plane .service whose .timer is unverified is unsupervised."""
    helper = ROOT_HELPER.read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "bootstrap-control-plane.sh").read_text(encoding="utf-8")
    for timer in (
        "services/radon-ib-watchdog.timer",
        "services/radon-portfolio-sync.timer",
        "services/radon-refresh.timer",
        "services/radon-db-backup.timer",
        "services/radon-drift-audit.timer",
        "services/radon-nextjs-db-watchdog.timer",
    ):
        assert timer in helper, timer
        assert timer in bootstrap, timer
        target = "/etc/systemd/system/" + timer.split("/", 1)[1]
        assert target in helper, target
        assert target in bootstrap, target


# --- REL-054 (R-140): a hash-matching but BROKEN unit installs cleanly today.
# `daemon-reload` never fails on a malformed unit body, rollback never
# reinstalls unit files, and two core non-control-plane services are in scope.


def _analyze_stub(tmp_path: pathlib.Path, *, reject: str = "") -> pathlib.Path:
    """Fake `systemd-analyze verify <path>`: non-zero when the staged body
    contains the rejection marker."""
    stub = tmp_path / "systemd-analyze"
    _write_executable(
        stub,
        f"""#!/bin/bash
[[ "${{1:-}}" == "verify" ]] || exit 0
marker={shlex.quote(reject)}
[[ -n "$marker" ]] || exit 0
grep -q -- "$marker" "$2" && {{ echo "$2: bad unit" >&2; exit 1; }}
exit 0
""",
    )
    return stub


def test_nextjs_and_newsfeed_are_installed_by_the_deploy(tmp_path):
    """nextjs and newsfeed are not control-plane and not the Gateway.
    install-units must copy them so EnvironmentFile/ExecStart edits do
    not require a hand `install` as root (P2 gap)."""
    box = Sandbox(tmp_path)
    for name in ("radon-nextjs.service", "radon-newsfeed.service"):
        box.source(name, SERVICE_BODY)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.unit_dir / "radon-nextjs.service").read_text() == SERVICE_BODY
    assert (box.unit_dir / "radon-newsfeed.service").read_text() == SERVICE_BODY
    assert box.systemctl_calls().count("daemon-reload") == 1
    assert "release-managed" not in result.stdout


def test_unit_that_fails_systemd_analyze_is_not_promoted(tmp_path):
    """A hash-matching unit body that systemd cannot parse installs cleanly
    today — `daemon-reload` does not fail on it, and rollback never
    reinstalls unit files, so the service stays down until a human SSHes."""
    box = Sandbox(tmp_path)
    broken = SERVICE_BODY.replace("ExecStart=/bin/true", "ExecStart=/nonexistent")
    box.source("radon-credit-spread.service", broken)
    box.env["RADON_TEST_SYSTEMD_ANALYZE"] = str(
        _analyze_stub(tmp_path, reject="/nonexistent")
    )

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (box.unit_dir / "radon-credit-spread.service").exists()
    assert "verify-failed" in result.stdout
    assert box.systemctl_calls() == []


def test_a_live_unit_is_restored_when_its_replacement_fails_verify(tmp_path):
    box = Sandbox(tmp_path)
    broken = SERVICE_BODY.replace("ExecStart=/bin/true", "ExecStart=/nonexistent")
    box.source("radon-credit-spread.service", broken)
    box.installed("radon-credit-spread.service", SERVICE_BODY)
    box.env["RADON_TEST_SYSTEMD_ANALYZE"] = str(
        _analyze_stub(tmp_path, reject="/nonexistent")
    )

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.unit_dir / "radon-credit-spread.service").read_text() == SERVICE_BODY
    assert "verify-failed" in result.stdout
    assert box.systemctl_calls() == []


def test_a_good_unit_still_installs_when_verify_is_available(tmp_path):
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.timer", TIMER_BODY)
    box.env["RADON_TEST_SYSTEMD_ANALYZE"] = str(_analyze_stub(tmp_path))

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.unit_dir / "radon-credit-spread.timer").read_text() == TIMER_BODY
    assert "installed=1" in result.stdout
