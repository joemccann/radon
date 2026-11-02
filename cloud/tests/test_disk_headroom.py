"""Threshold-triggered disk reclaim (2026-11-01, bottleneck track "disk").

radon-app root crossed 91.8% (3.1 GiB free) on 2026-10-31 and a deploy was
refused at 6.2G free against a ~8.3G model-preparation admission. The only
reclaim was the Sunday 03:20 UTC sweep, so a human ran the oneshot by hand.

The same unit now also fires hourly as a GUARD: it reclaims only when free
space is under HEADROOM_THRESHOLD_BYTES, and the deploy supervisor runs the
same guard under its held lock before it pulls or admits anything. When a
reclaim cannot restore headroom the disk-cleanup heartbeat goes to `error`,
which the watchdog error bucket already pages on.
"""

from __future__ import annotations

import fcntl
import importlib.util
import os
import pathlib
import time
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT.parent
GIB = 1024**3
DAY = 86400


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "disk_cleanup_headroom", ROOT / "scripts" / "disk_cleanup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


disk_cleanup = _load_module()

SUNDAY_SLOT = datetime(2026, 11, 8, 3, 22, tzinfo=timezone.utc)
TUESDAY_RTH = datetime(2026, 11, 3, 14, 50, tzinfo=timezone.utc)
SUNDAY_GUARD = datetime(2026, 11, 8, 4, 50, tzinfo=timezone.utc)
TIMER = "radon-disk-cleanup.timer"


def _ok_outcome(after=20 * GIB):
    return {
        "state": "ok",
        "detail": {
            "summary": "x", "free_before_bytes": 1, "free_after_bytes": after,
            "reclaimed_bytes": 0, "categories": {}, "notes": {}, "errors": {},
        },
    }


class TestThreshold:
    def test_threshold_covers_the_deploys_measured_requirement(self):
        # 2026-10-31: model admission needed ~8.3G after the pull; the next
        # release set adds ~3.4G of unique layers (podman system df,
        # 2026-11-01). Anything below their sum lets a deploy start that the
        # admission will refuse.
        assert disk_cleanup.HEADROOM_THRESHOLD_BYTES >= int(8.3e9) + int(3.4e9)
        assert disk_cleanup.HEADROOM_THRESHOLD_BYTES == 12 * GIB

    @pytest.mark.parametrize(
        "free,expected",
        [(0, True), (12 * GIB - 1, True), (12 * GIB, False), (40 * GIB, False)],
    )
    def test_needs_reclaim_is_strictly_below_the_threshold(self, free, expected):
        assert disk_cleanup.needs_reclaim(free, 12 * GIB) is expected


class TestModeResolution:
    def test_a_manual_start_is_always_the_full_sweep(self):
        # The operator's `systemctl start radon-disk-cleanup.service` keeps
        # meaning "sweep now", whatever the free space.
        assert disk_cleanup.resolve_mode([], "", TUESDAY_RTH, 50 * GIB) == "sweep"

    def test_the_sunday_timer_slot_is_the_full_sweep(self):
        assert disk_cleanup.resolve_mode([], TIMER, SUNDAY_SLOT, 50 * GIB) == "sweep"

    def test_an_hourly_firing_with_headroom_skips(self):
        assert disk_cleanup.resolve_mode([], TIMER, TUESDAY_RTH, 12 * GIB) == "skip"

    def test_an_hourly_firing_under_the_threshold_reclaims(self):
        assert disk_cleanup.resolve_mode([], TIMER, TUESDAY_RTH, 6 * GIB) == "reclaim"

    def test_a_sunday_firing_outside_the_slot_is_still_a_guard(self):
        assert disk_cleanup.resolve_mode([], TIMER, SUNDAY_GUARD, 50 * GIB) == "skip"

    @pytest.mark.parametrize("flag", ["--if-below", "--deploy-locked"])
    def test_guard_flags_force_threshold_mode(self, flag):
        assert disk_cleanup.resolve_mode([flag], "", TUESDAY_RTH, 50 * GIB) == "skip"
        assert disk_cleanup.resolve_mode([flag], "", TUESDAY_RTH, 3 * GIB) == "reclaim"

    def test_the_weekly_slot_is_sunday_03_utc_only(self):
        assert disk_cleanup.is_weekly_sweep_slot(SUNDAY_SLOT)
        assert not disk_cleanup.is_weekly_sweep_slot(SUNDAY_GUARD)
        assert not disk_cleanup.is_weekly_sweep_slot(TUESDAY_RTH)


class TestHeadroomIsReported:
    def test_a_reclaim_that_cannot_restore_headroom_is_an_error(self):
        outcome = disk_cleanup.run_cleanup(
            categories=(("journal", lambda: (250 * 1024**2, "vacuumed")),),
            free_space=iter([10 * GIB, 10 * GIB + 250 * 1024**2]).__next__,
            threshold=12 * GIB,
        )
        assert outcome["state"] == "error"
        message = outcome["detail"]["errors"]["headroom"]
        assert "below" in message and "12.0 GiB" in message
        assert "failed: headroom" in outcome["detail"]["summary"]

    def test_a_reclaim_that_restores_headroom_is_ok(self):
        outcome = disk_cleanup.run_cleanup(
            categories=(("journal", lambda: (3 * GIB, "vacuumed")),),
            free_space=iter([10 * GIB, 13 * GIB]).__next__,
            threshold=12 * GIB,
        )
        assert outcome["state"] == "ok"
        assert "headroom" not in outcome["detail"]["errors"]


class TestGuardEntryPoint:
    def _wire(self, monkeypatch, tmp_path, free):
        lock = tmp_path / "deploy.lock"
        lock.write_text("", encoding="utf-8")
        monkeypatch.setattr(disk_cleanup, "DEPLOY_LOCK_FILE", lock)
        monkeypatch.setattr(disk_cleanup, "_free_space", lambda: free)
        writes, runs = [], []
        monkeypatch.setattr(
            disk_cleanup, "write_service_health",
            lambda state, detail, started: writes.append((state, detail)),
        )
        monkeypatch.setattr(
            disk_cleanup, "run_cleanup",
            lambda categories=None, **_k: (runs.append(categories), _ok_outcome())[1],
        )
        return lock, writes, runs

    def test_headroom_skip_takes_no_lock_and_writes_no_heartbeat(self, monkeypatch, tmp_path):
        _lock, writes, runs = self._wire(monkeypatch, tmp_path, 40 * GIB)
        monkeypatch.setattr(
            disk_cleanup, "acquire_deploy_lock",
            lambda *_a, **_k: pytest.fail("a healthy guard firing must not touch the deploy lock"),
        )
        assert disk_cleanup.main(["--if-below"]) == 0
        assert runs == [] and writes == []

    def test_under_threshold_reclaims_and_heartbeats(self, monkeypatch, tmp_path):
        _lock, writes, runs = self._wire(monkeypatch, tmp_path, 5 * GIB)
        assert disk_cleanup.main(["--if-below"]) == 0
        assert len(runs) == 1 and writes and writes[0][0] == "ok"

    def test_a_timer_firing_off_slot_is_a_guard(self, monkeypatch, tmp_path):
        _lock, writes, runs = self._wire(monkeypatch, tmp_path, 40 * GIB)
        monkeypatch.setenv("TRIGGER_UNIT", TIMER)
        monkeypatch.setattr(disk_cleanup, "_utcnow", lambda: TUESDAY_RTH)
        assert disk_cleanup.main([]) == 0
        assert runs == [] and writes == []

    def test_the_guard_does_not_wait_out_a_deploy(self, monkeypatch, tmp_path):
        lock, writes, runs = self._wire(monkeypatch, tmp_path, 5 * GIB)
        monkeypatch.setattr(disk_cleanup, "DEPLOY_LOCK_POLL_S", 0.0)
        holder = open(lock, "r")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            started = time.monotonic()
            assert disk_cleanup.main(["--if-below"]) == disk_cleanup.EX_TEMPFAIL
            assert time.monotonic() - started < 5
        finally:
            holder.close()
        assert runs == [] and writes == []


class TestDeployLockedReclaim:
    """`radon-app-runtime reclaim-locked` hands the supervisor's locked FD 9
    over as stdin. A second open of the lock would conflict with that hold."""

    def _wire(self, monkeypatch, tmp_path, free):
        lock = tmp_path / "deploy.lock"
        lock.write_text("", encoding="utf-8")
        monkeypatch.setattr(disk_cleanup, "DEPLOY_LOCK_FILE", lock)
        monkeypatch.setattr(disk_cleanup, "_free_space", lambda: free)
        writes, runs = [], []
        monkeypatch.setattr(
            disk_cleanup, "write_service_health",
            lambda state, detail, started: writes.append(state),
        )
        monkeypatch.setattr(
            disk_cleanup, "run_cleanup",
            lambda categories=None, **_k: (runs.append(categories), _ok_outcome())[1],
        )
        return lock, writes, runs

    def test_the_held_lock_on_stdin_is_accepted(self, monkeypatch, tmp_path):
        lock, writes, runs = self._wire(monkeypatch, tmp_path, 5 * GIB)
        holder = open(lock, "r")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            monkeypatch.setattr(disk_cleanup, "LOCK_STDIN_FD", holder.fileno())
            assert disk_cleanup.main(["--deploy-locked"]) == 0
        finally:
            holder.close()
        assert len(runs) == 1 and writes == ["ok"]
        names = [name for name, _ in runs[0]]
        # The supervisor already ran prune-locked <target>; a target-less
        # app-image prune here would drop the pair the deploy is about to run.
        assert "app-images" not in names
        assert {"dangling-images", "journal", "rotated-logs"} <= set(names)

    def test_a_lock_held_through_another_open_is_refused(self, monkeypatch, tmp_path):
        lock, writes, runs = self._wire(monkeypatch, tmp_path, 5 * GIB)
        other = open(lock, "r")
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mine = open(lock, "r")
        try:
            monkeypatch.setattr(disk_cleanup, "LOCK_STDIN_FD", mine.fileno())
            assert disk_cleanup.main(["--deploy-locked"]) == disk_cleanup.EX_TEMPFAIL
        finally:
            mine.close()
            other.close()
        assert runs == [] and writes == []

    def test_stdin_that_is_not_the_lock_is_refused(self, monkeypatch, tmp_path):
        _lock, writes, runs = self._wire(monkeypatch, tmp_path, 5 * GIB)
        imposter = tmp_path / "imposter"
        imposter.write_text("", encoding="utf-8")
        handle = open(imposter, "r")
        try:
            monkeypatch.setattr(disk_cleanup, "LOCK_STDIN_FD", handle.fileno())
            assert disk_cleanup.main(["--deploy-locked"]) == disk_cleanup.EX_TEMPFAIL
        finally:
            handle.close()
        assert runs == []

    def test_headroom_skips_before_checking_the_lock(self, monkeypatch, tmp_path):
        _lock, writes, runs = self._wire(monkeypatch, tmp_path, 40 * GIB)
        monkeypatch.setattr(disk_cleanup, "LOCK_STDIN_FD", 987654)
        assert disk_cleanup.main(["--deploy-locked"]) == 0
        assert runs == [] and writes == []


class TestRotatedLogs:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("syslog.1", True),
            ("auth.log.2.gz", True),
            ("btmp.1", True),
            ("dpkg.log.12.gz", True),
            ("syslog", False),
            ("auth.log", False),
            ("radon-2026-10-26T15-16-19.696-size.log.gz", False),
            ("system@0001.journal", False),
        ],
    )
    def test_only_numbered_rotations_match(self, name, expected):
        assert disk_cleanup.is_rotated_log_name(name) is expected

    def _tree(self, tmp_path):
        base = tmp_path / "log"
        (base / "apt").mkdir(parents=True)
        (base / "journal").mkdir()
        old = time.time() - 30 * DAY
        files = {
            "syslog.2.gz": old,
            "syslog.1": time.time(),
            "syslog": old,
            "apt/term.log.1.gz": old,
            "journal/system.1": old,
        }
        for rel, mtime in files.items():
            path = base / rel
            path.write_bytes(b"x" * 100)
            os.utime(path, (mtime, mtime))
        return base

    def test_removes_old_rotations_only_and_never_the_journal(self, tmp_path, monkeypatch):
        base = self._tree(tmp_path)
        monkeypatch.setattr(disk_cleanup, "LOG_DIR", base)
        monkeypatch.setattr(disk_cleanup, "ROTATED_LOG_DIR_OWNER", os.geteuid())
        freed, note = disk_cleanup.cleanup_rotated_logs()
        assert freed == 200
        assert not (base / "syslog.2.gz").exists()
        assert not (base / "apt/term.log.1.gz").exists()
        assert (base / "syslog.1").exists(), "a rotation younger than the age floor stays"
        assert (base / "syslog").exists(), "the live log is never touched"
        assert (base / "journal/system.1").exists(), "journald owns /var/log/journal"
        assert "2" in note

    def test_a_directory_owned_by_another_account_is_skipped(self, tmp_path, monkeypatch):
        base = self._tree(tmp_path)
        monkeypatch.setattr(disk_cleanup, "LOG_DIR", base)
        monkeypatch.setattr(disk_cleanup, "ROTATED_LOG_DIR_OWNER", os.geteuid() + 1)
        freed, _note = disk_cleanup.cleanup_rotated_logs()
        assert freed == 0
        assert (base / "syslog.2.gz").exists()

    def test_a_symlinked_rotation_is_never_followed(self, tmp_path, monkeypatch):
        base = self._tree(tmp_path)
        victim = tmp_path / "victim"
        victim.write_bytes(b"keep")
        (base / "evil.1").symlink_to(victim)
        os.utime(base / "evil.1", (0, 0), follow_symlinks=False)
        monkeypatch.setattr(disk_cleanup, "LOG_DIR", base)
        monkeypatch.setattr(disk_cleanup, "ROTATED_LOG_DIR_OWNER", os.geteuid())
        disk_cleanup.cleanup_rotated_logs()
        assert victim.read_bytes() == b"keep"

    def test_dry_run_deletes_nothing(self, tmp_path, monkeypatch):
        base = self._tree(tmp_path)
        monkeypatch.setattr(disk_cleanup, "LOG_DIR", base)
        monkeypatch.setattr(disk_cleanup, "ROTATED_LOG_DIR_OWNER", os.geteuid())
        monkeypatch.setattr(disk_cleanup, "DRY_RUN", True)
        freed, _note = disk_cleanup.cleanup_rotated_logs()
        assert freed == 200
        assert (base / "syslog.2.gz").exists()

    def test_the_category_is_in_the_sweep(self):
        assert dict(disk_cleanup.CATEGORIES)["rotated-logs"] is disk_cleanup.cleanup_rotated_logs


class TestSubprocessesDoNotInheritTheLock:
    def test_run_closes_stdin(self, monkeypatch):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)

            class Result:
                stdout = ""
            return Result()

        monkeypatch.setattr(disk_cleanup.subprocess, "run", fake_run)
        disk_cleanup._run(["true"])
        assert seen.get("stdin") is disk_cleanup.subprocess.DEVNULL


class TestUnitWiring:
    TIMER_FILE = ROOT / "services" / "radon-disk-cleanup.timer"

    def _code(self, path):
        return "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_timer_keeps_the_weekly_slot_and_adds_an_hourly_guard(self):
        code = self._code(self.TIMER_FILE)
        assert "OnCalendar=Sun *-*-* 03:20:00 UTC" in code
        assert "OnCalendar=*-*-* *:50:00 UTC" in code
        assert "Persistent=true" not in code

    def test_the_hourly_guard_never_lands_in_the_weekly_slot(self):
        # RandomizedDelaySec spreads :50 to at most :55, never into 03:xx.
        code = self._code(self.TIMER_FILE)
        assert "RandomizedDelaySec=300" in code
        assert not disk_cleanup.is_weekly_sweep_slot(
            datetime(2026, 11, 8, 2, 55, tzinfo=timezone.utc)
        )

    def test_runtime_exposes_an_argumentless_reclaim_locked_verb(self):
        runtime = (ROOT / "scripts" / "radon-app-runtime.sh").read_text(encoding="utf-8")
        assert "reclaim-locked)\n    [[ $# -eq 1 ]] || usage\n    cmd_reclaim_locked" in runtime
        assert "/usr/local/lib/radon/disk_cleanup.py --deploy-locked" in runtime
        assert "RADON_ENV_FILE=/etc/radon/env" in runtime

    def test_sudoers_grants_reclaim_locked_without_arguments(self):
        sudoers = (ROOT / "config" / "sudoers.d" / "radon-deploy").read_text(encoding="utf-8")
        assert "/usr/local/sbin/radon-app-runtime reclaim-locked," in sudoers \
            or sudoers.rstrip().endswith("/usr/local/sbin/radon-app-runtime reclaim-locked")
        assert "reclaim-locked *" not in sudoers
        assert "reclaim-locked [" not in sudoers
