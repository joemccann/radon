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


# ── R-342: the registry-outage fallback tag is never pruned ────────────────

def _image(repo: str, tag: str, created: float, size: int = 1):
    return disk_cleanup.Image(
        repository=repo, tag=tag, image_id="sha256:x",
        created_at=created, size_bytes=size,
    )


class TestFallbackTagIsKept:
    REPO = "ghcr.io/joemccann/radon-node"
    HEAD = "a" * 40

    def test_latest_is_never_selected_for_removal(self):
        images = [
            _image(self.REPO, self.HEAD, 500.0),
            _image(self.REPO, "b" * 40, 400.0),
            _image(self.REPO, "c" * 40, 300.0),
            _image(self.REPO, "d" * 40, 200.0),
            _image(self.REPO, "latest", 1.0),
        ]
        tags = {i.tag for i in disk_cleanup.select_prunable_images(images, self.HEAD)}
        assert "latest" not in tags, (
            "radon-app-runtime.sh resolve_image falls back to "
            "RADON_APP_IMAGE_FALLBACK_TAG when the pinned SHA manifest probe "
            "fails; deleting it leaves no local copy for the next GHCR outage"
        )
        assert "d" * 40 in tags, "genuinely stale SHA pairs are still prunable"

    def test_the_keep_set_honours_the_configured_fallback_tag(self, monkeypatch):
        monkeypatch.setenv("RADON_APP_IMAGE_FALLBACK_TAG", "stable")
        images = [
            _image(self.REPO, self.HEAD, 500.0),
            _image(self.REPO, "b" * 40, 400.0),
            _image(self.REPO, "c" * 40, 300.0),
            _image(self.REPO, "stable", 1.0),
            _image(self.REPO, "d" * 40, 200.0),
        ]
        tags = {i.tag for i in disk_cleanup.select_prunable_images(images, self.HEAD)}
        assert "stable" not in tags


# ── R-371: one undeletable image must not abort the loop ───────────────────

class TestImageRemovalIsPerImage:
    def test_a_failing_rm_does_not_skip_the_remaining_images(self, monkeypatch):
        repo = "ghcr.io/joemccann/radon-node"
        head = "a" * 40
        # KEEP_IMAGE_PAIRS retains the two newest, so six tags leave four prunable.
        images = [_image(repo, chr(98 + i) * 40, 100.0 - i, size=10) for i in range(6)]
        monkeypatch.setattr(
            disk_cleanup, "resolve_head_sha", lambda _p: head
        )
        monkeypatch.setattr(disk_cleanup, "parse_image_lines", lambda _o: images)

        calls: list[str] = []

        def _run(argv):
            if argv[:2] == [disk_cleanup.DOCKER_BIN, "image"] and argv[2] == "rm":
                calls.append(argv[3])
                if len(calls) == 2:
                    raise RuntimeError("image is referenced by a stopped container")
                return ""
            return ""

        monkeypatch.setattr(disk_cleanup, "_run", _run)
        reclaimed, note = disk_cleanup.cleanup_docker_images()

        assert len(calls) == 4, (
            "check=True aborted the loop on the first failure, so a single "
            f"referenced image meant zero reclaimed week after week; {calls}"
        )
        assert reclaimed == 30, "only the three that actually went are counted"
        assert "1 failed" in note or "failed" in note


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
