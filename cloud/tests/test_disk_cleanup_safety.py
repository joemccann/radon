"""R-340 / R-341 / R-343 / R-370 / REL-123: the weekly sweep cannot delete the
rollback source, race a deploy, or be walked through a symlink.

R-340: `cleanup_release_worktrees` deletes any `backup.<sha12>.XXXXXX` older
than 3 days with no awareness of the durable deploy transition journal — which
is exactly the rollback source. A deploy that dies mid-transition leaves
`/home/radon/.radon-deploy-transition.json` pending with `backup_dir` under
`~/.radon-releases/`; if nobody runs recovery before Sunday 03:20 UTC the name
matches, the mtime is stale, and it is rmtree'd. `restore_release_backup` then
finds nothing and rollback is impossible on a live trading host.

R-343: `is_protected_path` compares UNRESOLVED paths and `remove_tree` checks
`is_symlink()` on the final component only, so an ANCESTOR symlink defeats
both — the exact escape the module docstring claims to prevent. The radon
account owns /home/radon and is the adversary that docstring names.

R-370: stale worktree admin dirs were removed with a bare
`shutil.rmtree(admin, ignore_errors=True)` that bypasses `remove_tree`'s
protected-path check entirely, deleting inside `/home/radon/radon/.git` — the
tree the docstring asserts is unreachable and which `is_protected_path` WOULD
have refused.

R-341 (the flock) is asserted against the unit file, since the lock is taken
by systemd exactly as `radon-db-backup.service` takes its own.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = 86400


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "disk_cleanup_safety", ROOT / "scripts" / "disk_cleanup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


disk_cleanup = _load_module()


# ── R-340: a pending transition journal protects its own backup ────────────

class TestPendingTransitionProtectsTheRollbackSource:
    def _journal(self, tmp_path, backup_dir, stage_dir=None, phase="promoting"):
        path = tmp_path / "transition.json"
        path.write_text(
            json.dumps(
                {
                    "requested_sha": "b" * 40,
                    "previous_sha": "a" * 40,
                    "stage_dir": str(stage_dir or (tmp_path / "stage.abc")),
                    "backup_dir": str(backup_dir),
                    "phase": phase,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_the_pending_backup_dir_is_not_selected(self, tmp_path, monkeypatch):
        releases = tmp_path / ".radon-releases"
        releases.mkdir()
        backup = releases / f"backup.{'a' * 12}.XXXXXX"
        backup.mkdir()
        stage = releases / f"stage.{'a' * 12}.YYYYYY"
        stage.mkdir()
        other = releases / f"backup.{'f' * 12}.ZZZZZZ"
        other.mkdir()

        journal = self._journal(tmp_path, backup, stage_dir=stage)
        monkeypatch.setattr(disk_cleanup, "TRANSITION_JOURNAL_FILE", journal)

        now = 1_000_000.0
        entries = [(p, now - 4 * DAY) for p in (backup, stage, other)]
        selected = disk_cleanup.select_prunable_releases(
            entries, now, releases_dir=releases
        )

        assert backup not in selected, (
            "the pending transition journal names this directory as the "
            "rollback source; deleting it makes rollback impossible"
        )
        assert stage not in selected, "the in-flight stage dir is equally live"
        assert other in selected, "an unrelated stale orphan is still prunable"

    def test_no_journal_leaves_the_original_selection_untouched(self, tmp_path, monkeypatch):
        releases = tmp_path / ".radon-releases"
        releases.mkdir()
        orphan = releases / f"backup.{'a' * 12}.XXXXXX"
        orphan.mkdir()
        monkeypatch.setattr(
            disk_cleanup, "TRANSITION_JOURNAL_FILE", tmp_path / "absent.json"
        )
        now = 1_000_000.0
        assert disk_cleanup.select_prunable_releases(
            [(orphan, now - 4 * DAY)], now, releases_dir=releases
        ) == [orphan]

    def test_an_unreadable_journal_fails_closed(self, tmp_path, monkeypatch):
        releases = tmp_path / ".radon-releases"
        releases.mkdir()
        orphan = releases / f"backup.{'a' * 12}.XXXXXX"
        orphan.mkdir()
        bad = tmp_path / "transition.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(disk_cleanup, "TRANSITION_JOURNAL_FILE", bad)
        now = 1_000_000.0
        assert disk_cleanup.select_prunable_releases(
            [(orphan, now - 4 * DAY)], now, releases_dir=releases
        ) == [], "a journal that exists but cannot be read means a deploy is mid-flight"


# ── R-343: an ANCESTOR symlink must not defeat the guards ──────────────────

class TestAncestorSymlinkCannotEscape:
    def test_remove_tree_refuses_a_path_reached_through_a_symlink(
        self, tmp_path, monkeypatch
    ):
        real = tmp_path / "var-lib-radon"
        real.mkdir()
        (real / "pip").mkdir()
        (real / "pip" / "wheel").write_text("x", encoding="utf-8")

        home = tmp_path / "home-radon"
        home.mkdir()
        link = home / ".cache"
        link.symlink_to(real, target_is_directory=True)

        # `real` stands in for a protected tree; the candidate's LEAF is a real
        # directory and its string form compares clean.
        monkeypatch.setattr(disk_cleanup, "PROTECTED_PATHS", (real,))

        with pytest.raises(ValueError, match="protected|symlink"):
            disk_cleanup.remove_tree(link / "pip")

        assert (real / "pip" / "wheel").exists(), "root rmtree'd through the symlink"

    def test_is_protected_path_resolves_before_comparing(self, tmp_path, monkeypatch):
        real = tmp_path / "protected"
        real.mkdir()
        (real / "inner").mkdir()
        link = tmp_path / "innocent"
        link.symlink_to(real, target_is_directory=True)
        monkeypatch.setattr(disk_cleanup, "PROTECTED_PATHS", (real,))

        assert disk_cleanup.is_protected_path(link / "inner") is True

    def test_an_ordinary_unprotected_tree_is_still_removable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(disk_cleanup, "PROTECTED_PATHS", (tmp_path / "keep",))
        victim = tmp_path / "cache"
        victim.mkdir()
        (victim / "blob").write_text("0123456789", encoding="utf-8")
        assert disk_cleanup.remove_tree(victim) > 0
        assert not victim.exists()


# ── R-370: admin-dir removal goes through the guard ────────────────────────

class TestAdminDirRemovalIsGuarded:
    def test_an_admin_dir_under_the_live_git_tree_is_refused(self, tmp_path, monkeypatch):
        live = tmp_path / "radon"
        admin = live / ".git" / "worktrees" / "stale"
        admin.mkdir(parents=True)
        monkeypatch.setattr(disk_cleanup, "LIVE_TREE", live)
        monkeypatch.setattr(disk_cleanup, "PROTECTED_PATHS", (live,))

        with pytest.raises(ValueError, match="protected"):
            disk_cleanup.remove_tree(admin)

    def test_the_module_does_not_call_bare_rmtree_on_an_admin_dir(self):
        """Structural: comment lines stripped before asserting."""
        source = (ROOT / "scripts" / "disk_cleanup.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert "shutil.rmtree(admin, ignore_errors=True)" not in code, (
            "the bare rmtree bypasses is_protected_path entirely and reaches "
            "inside /home/radon/radon/.git"
        )


# ── R-341: the unit takes the deploy lock ──────────────────────────────────

class TestTheSweepTakesTheDeployLock:
    UNIT = ROOT / "services" / "radon-disk-cleanup.service"

    def test_a_held_deploy_lock_skips_the_sweep_with_ex_tempfail(self, tmp_path, monkeypatch):
        import fcntl

        lock = tmp_path / "deploy.lock"
        lock.write_text("", encoding="utf-8")
        holder = open(lock, "r")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            monkeypatch.setattr(disk_cleanup, "DEPLOY_LOCK_FILE", lock)
            monkeypatch.setattr(disk_cleanup, "DEPLOY_LOCK_TIMEOUT_S", 0.0)
            monkeypatch.setattr(disk_cleanup, "DEPLOY_LOCK_POLL_S", 0.0)
            monkeypatch.setattr(
                disk_cleanup, "run_cleanup",
                lambda *_a, **_k: pytest.fail("swept while a deploy held the lock"),
            )
            assert disk_cleanup.main([]) == disk_cleanup.EX_TEMPFAIL
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()

    def test_an_unlocked_host_still_sweeps(self, tmp_path, monkeypatch):
        lock = tmp_path / "deploy.lock"
        lock.write_text("", encoding="utf-8")
        monkeypatch.setattr(disk_cleanup, "DEPLOY_LOCK_FILE", lock)
        ran = []
        monkeypatch.setattr(
            disk_cleanup, "run_cleanup",
            lambda *_a, **_k: (
                ran.append(1),
                {
                    "state": "ok",
                    "detail": {
                        "summary": "x", "free_before_bytes": 1, "free_after_bytes": 2,
                        "reclaimed_bytes": 1, "categories": {}, "notes": {}, "errors": {},
                    },
                },
            )[1],
        )
        monkeypatch.setattr(disk_cleanup, "write_service_health", lambda *_a, **_k: None)
        disk_cleanup.main([])
        assert ran == [1]

    def test_root_refuses_to_follow_a_symlinked_lock_file(self, tmp_path, monkeypatch):
        """The lock lives under /home/radon, which the radon account owns."""
        target = tmp_path / "elsewhere"
        target.write_text("", encoding="utf-8")
        link = tmp_path / "deploy.lock"
        link.symlink_to(target)
        monkeypatch.setattr(disk_cleanup, "DEPLOY_LOCK_FILE", link)
        monkeypatch.setattr(
            disk_cleanup, "run_cleanup",
            lambda *_a, **_k: pytest.fail("swept through a symlinked lock file"),
        )
        assert disk_cleanup.main([]) == disk_cleanup.EX_TEMPFAIL

    def test_the_unit_tolerates_the_temp_fail_exit(self):
        text = self.UNIT.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        assert "SuccessExitStatus=75" in code, (
            "a skipped run because a deploy held the lock is not a failure"
        )

    def test_the_root_exec_start_names_no_radon_writable_path(self):
        """cloud/tests/test_root_execution_paths.py forbids it, which is why
        the lock is taken in-process rather than by an ExecStart flock."""
        exec_start = next(
            line for line in self.UNIT.read_text(encoding="utf-8").splitlines()
            if line.startswith("ExecStart=")
        )
        assert "/home/radon" not in exec_start


# ── R-368: a ceiling and a dry run ─────────────────────────────────────────

class TestReclaimCeiling:
    def test_a_category_over_the_path_ceiling_errors_rather_than_deleting(
        self, tmp_path, monkeypatch
    ):
        releases = tmp_path / ".radon-releases"
        releases.mkdir()
        paths = []
        for i in range(disk_cleanup.MAX_PRUNE_PATHS + 1):
            d = releases / f"backup.{i:012d}.XXXXXX"
            d.mkdir()
            paths.append(d)

        monkeypatch.setattr(disk_cleanup, "RELEASES_DIR", releases)
        monkeypatch.setattr(
            disk_cleanup, "TRANSITION_JOURNAL_FILE", tmp_path / "absent.json"
        )
        monkeypatch.setattr(
            disk_cleanup, "select_prunable_releases", lambda *_a, **_k: paths
        )

        with pytest.raises(disk_cleanup.ReclaimCeilingExceeded):
            disk_cleanup.cleanup_release_worktrees()

        assert all(p.exists() for p in paths), "the ceiling must refuse, not delete"

    def test_dry_run_deletes_nothing_and_still_reports(self, tmp_path, monkeypatch):
        victim = tmp_path / "cache"
        victim.mkdir()
        (victim / "blob").write_text("0123456789", encoding="utf-8")
        monkeypatch.setattr(disk_cleanup, "PROTECTED_PATHS", ())
        monkeypatch.setattr(disk_cleanup, "DRY_RUN", True)

        freed = disk_cleanup.remove_tree(victim)

        assert freed > 0, "a dry run still reports what it WOULD reclaim"
        assert victim.exists(), "a dry run deletes nothing"

    def test_main_accepts_a_dry_run_flag(self):
        source = (ROOT / "scripts" / "disk_cleanup.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert "--dry-run" in code


# ── R-369: no boot-time replay of a destructive weekly job ─────────────────

class TestTimerDoesNotReplayAtBoot:
    TIMER = ROOT / "services" / "radon-disk-cleanup.timer"

    def test_persistent_is_not_set(self):
        code = "\n".join(
            line for line in self.TIMER.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "Persistent=true" not in code, (
            "a Tuesday 14:30 UTC reboot after a missed Sunday run replays the "
            "sweep during US market hours, voiding the timer's own "
            "'clear of every DB-maintenance window' safety argument"
        )

    def test_the_weekly_slot_is_unchanged(self):
        text = self.TIMER.read_text(encoding="utf-8")
        assert "OnCalendar=Sun *-*-* 03:20:00 UTC" in text


# ── an ancestor swapped AFTER the symlink check must not redirect the delete ─

class TestAncestorSwapAfterCheckCannotRedirect:
    def _swap_after_check(self, monkeypatch, ancestor, elsewhere):
        real_check = disk_cleanup.has_symlink_component

        def check_then_swap(path):
            verdict = real_check(path)
            ancestor.rename(ancestor.with_name(ancestor.name + ".moved"))
            ancestor.symlink_to(elsewhere, target_is_directory=True)
            return verdict

        monkeypatch.setattr(disk_cleanup, "has_symlink_component", check_then_swap)

    def test_a_swapped_ancestor_does_not_delete_the_same_named_tree_elsewhere(
        self, tmp_path, monkeypatch
    ):
        elsewhere = tmp_path / "var-lib-radon"
        (elsewhere / "pip").mkdir(parents=True)
        (elsewhere / "pip" / "wheel").write_text("keep", encoding="utf-8")
        cache = tmp_path / "home-radon" / ".cache"
        (cache / "pip").mkdir(parents=True)
        (cache / "pip" / "blob").write_text("x", encoding="utf-8")
        monkeypatch.setattr(disk_cleanup, "PROTECTED_PATHS", ())
        self._swap_after_check(monkeypatch, cache, elsewhere)

        with pytest.raises(OSError):
            disk_cleanup.remove_tree(cache / "pip")

        assert (elsewhere / "pip" / "wheel").exists(), "deleted through a swapped ancestor"

    def test_a_platform_without_fd_safe_rmtree_fails_closed(self, tmp_path, monkeypatch):
        victim = tmp_path / "cache"
        victim.mkdir()
        (victim / "blob").write_text("x", encoding="utf-8")
        monkeypatch.setattr(disk_cleanup, "PROTECTED_PATHS", ())
        monkeypatch.setattr(disk_cleanup, "_SAFE_RMTREE", False)

        with pytest.raises(OSError, match="symlink-safe"):
            disk_cleanup.remove_tree(victim)

        assert (victim / "blob").exists()

    def test_a_vanished_leaf_reclaims_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(disk_cleanup, "PROTECTED_PATHS", ())
        assert disk_cleanup.remove_tree(tmp_path / "absent") == 0


# ── app images: delegate to radon-app-runtime under the held deploy lock ────
# Since 6298d6806 the runtime prefers /usr/bin/podman, but this sweep still
# listed and removed images through /usr/bin/docker, so it never saw the
# podman store that holds every release set. The image keep-set now lives in
# one place, `radon-app-runtime prune-locked`, which this job calls with its
# own held deploy-lock descriptor as stdin (a second open would conflict with
# that lock and skip, #1012).

_FAKE_RUNTIME = """#!{python}
import fcntl, os, sys
lock = os.environ["FAKE_LOCK"]
a, b = os.fstat(0), os.stat(lock)
same = (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
fcntl.flock(0, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the caller's own lock: succeeds
fresh = open(lock)
try:
    fcntl.flock(fresh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    held = "free"
except BlockingIOError:
    held = "held"
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + f" stdin={{'lock' if same else 'other'}} {{held}}\\n")
print("radon-app-runtime: image prune skipped: example", file=sys.stderr)
"""


class TestAppImagesDelegateToTheRuntime:
    def _setup(self, tmp_path, monkeypatch):
        import sys

        lock = tmp_path / "deploy.lock"
        lock.write_text("", encoding="utf-8")
        log = tmp_path / "runtime.log"
        runtime = tmp_path / "radon-app-runtime"
        runtime.write_text(_FAKE_RUNTIME.format(python=sys.executable), encoding="utf-8")
        runtime.chmod(0o755)
        monkeypatch.setenv("FAKE_LOCK", str(lock))
        monkeypatch.setenv("FAKE_LOG", str(log))
        monkeypatch.setattr(disk_cleanup, "APP_RUNTIME_BIN", str(runtime))
        return lock, log

    def test_the_runtime_prunes_under_this_jobs_held_lock(self, tmp_path, monkeypatch):
        lock, log = self._setup(tmp_path, monkeypatch)
        with disk_cleanup.acquire_deploy_lock(path=lock, timeout_s=0.0) as held:
            assert held
            reclaimed, note = disk_cleanup.cleanup_app_images()
        assert log.read_text(encoding="utf-8").splitlines() == ["prune-locked stdin=lock held"]
        assert reclaimed >= 0
        assert "image prune skipped: example" in note

    def test_without_the_held_lock_the_runtime_is_never_called(self, tmp_path, monkeypatch):
        _lock, log = self._setup(tmp_path, monkeypatch)
        with pytest.raises(RuntimeError, match="deploy lock"):
            disk_cleanup.cleanup_app_images()
        assert not log.exists()

    def test_a_dry_run_never_calls_the_runtime(self, tmp_path, monkeypatch):
        lock, log = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(disk_cleanup, "DRY_RUN", True)
        with disk_cleanup.acquire_deploy_lock(path=lock, timeout_s=0.0):
            reclaimed, note = disk_cleanup.cleanup_app_images()
        assert reclaimed == 0 and "dry run" in note
        assert not log.exists()

    def test_the_categories_name_no_engine_specific_image_sweep(self):
        names = [name for name, _ in disk_cleanup.CATEGORIES]
        assert names[:2] == ["app-images", "dangling-images"]
        assert dict(disk_cleanup.CATEGORIES)["app-images"] is disk_cleanup.cleanup_app_images
        assert not hasattr(disk_cleanup, "select_prunable_images"), (
            "the image keep-set lives in radon-app-runtime; a second copy here drifted"
        )

    def test_the_root_runtime_path_is_the_control_plane_copy(self):
        assert disk_cleanup.APP_RUNTIME_BIN == "/usr/local/sbin/radon-app-runtime"


class TestDanglingLayersUseEveryInstalledEngine:
    def _engine(self, path, log):
        path.write_text(
            f"#!/bin/bash\nprintf '%s %s\\n' \"${{0##*/}}\" \"$*\" >> {log}\n", encoding="utf-8"
        )
        path.chmod(0o755)

    def test_podman_is_pruned_when_installed(self, tmp_path, monkeypatch):
        log = tmp_path / "engines.log"
        podman, docker = tmp_path / "podman", tmp_path / "docker"
        self._engine(podman, log)
        self._engine(docker, log)
        monkeypatch.setattr(disk_cleanup, "PODMAN_BIN", str(podman))
        monkeypatch.setattr(disk_cleanup, "DOCKER_BIN", str(docker))
        _reclaimed, note = disk_cleanup.cleanup_dangling_images()
        assert log.read_text(encoding="utf-8").splitlines() == [
            "podman image prune -f", "docker image prune -f",
        ]
        assert "podman" in note and "docker" in note

    def test_a_missing_engine_is_skipped(self, tmp_path, monkeypatch):
        log = tmp_path / "engines.log"
        podman = tmp_path / "podman"
        self._engine(podman, log)
        monkeypatch.setattr(disk_cleanup, "PODMAN_BIN", str(podman))
        monkeypatch.setattr(disk_cleanup, "DOCKER_BIN", str(tmp_path / "absent-docker"))
        disk_cleanup.cleanup_dangling_images()
        assert log.read_text(encoding="utf-8").splitlines() == ["podman image prune -f"]


def test_the_docstring_states_the_current_backup_retention():
    doc = disk_cleanup.__doc__
    assert "RETENTION_DAYS=30" not in doc
    assert "RETENTION_DAYS=7" in doc and "B2" in doc
