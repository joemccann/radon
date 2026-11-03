"""Tests for scripts/disk_cleanup.py pure logic (no docker, no host, no Turso).

2026-08-27: the watchdog `root-disk-usage` check paged P1 when the VPS root
filesystem reached 98% (1.7 GiB free on a 75G disk). Nothing on the box ever
pruned the per-SHA ``ghcr.io/joemccann/radon-{node,python}`` image pairs
(~5.8G per deploy), and ``deploy.sh``'s best-effort
``cleanup_staged_release_path`` had leaked seven ``stage.``/``backup.``
worktrees under /home/radon/.radon-releases going back to Jul 11.

Every destructive path decision this job makes is a PURE selector tested here:
the live /home/radon/radon checkout can never be selected as an orphaned
worktree. App images are pruned by `radon-app-runtime prune-locked`
(test_disk_cleanup_safety.py, test_app_runtime.py).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = 86400


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "disk_cleanup", ROOT / "scripts" / "disk_cleanup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


disk_cleanup = _load_module()

class TestReleaseWorktreeSelection:
    RELEASES = pathlib.Path("/home/radon/.radon-releases")
    NOW = 10 * DAY

    def _entries(self, *pairs):
        return [(self.RELEASES / name, mtime) for name, mtime in pairs]

    def test_never_returns_the_live_checkout(self):
        entries = [(pathlib.Path("/home/radon/radon"), 0.0)]
        assert disk_cleanup.select_prunable_releases(entries, self.NOW) == []

    def test_never_returns_a_path_outside_the_releases_dir(self):
        entries = [(pathlib.Path("/home/radon/stage.abc1234.AbCdEf"), 0.0)]
        assert disk_cleanup.select_prunable_releases(entries, self.NOW) == []

    def test_selects_stage_and_backup_orphans_past_the_age_threshold(self):
        entries = self._entries(
            ("stage.abc1234.AbCdEf", self.NOW - 9 * DAY),
            ("backup.abc1234.ZzYyXx", self.NOW - 4 * DAY),
        )
        removed = disk_cleanup.select_prunable_releases(entries, self.NOW, max_age_days=3)
        assert removed == [
            self.RELEASES / "backup.abc1234.ZzYyXx",
            self.RELEASES / "stage.abc1234.AbCdEf",
        ]

    def test_retains_a_worktree_younger_than_the_threshold(self):
        entries = self._entries(("stage.abc1234.AbCdEf", self.NOW - 2 * DAY))
        assert disk_cleanup.select_prunable_releases(entries, self.NOW, max_age_days=3) == []

    def test_ignores_names_deploy_sh_never_creates(self):
        entries = self._entries(
            ("notes.txt", 0.0),
            ("radon", 0.0),
            ("stage", 0.0),
        )
        assert disk_cleanup.select_prunable_releases(entries, self.NOW) == []

    def test_is_a_no_op_on_an_empty_releases_dir(self):
        assert disk_cleanup.select_prunable_releases([], self.NOW) == []


class TestProtectedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "/home/radon/radon",
            "/home/radon/radon/data",
            "/home/radon/radon-cloud/backups/db",
            "/home/radon/.cache/ms-playwright",
            "/home/radon/.cache/huggingface",
            "/home/radon/.cache/fastembed",
            "/home/radon/.cache/radon-wheels",
            # An ancestor of a protected path is protected: removing it takes
            # the protected tree with it.
            "/home/radon/.cache",
            "/home/radon",
            "/",
        ],
    )
    def test_protected(self, path):
        assert disk_cleanup.is_protected_path(pathlib.Path(path)) is True

    @pytest.mark.parametrize(
        "path",
        ["/home/radon/.npm/_cacache", "/home/radon/.cache/pip", "/home/radon/.radon-releases/stage.a.b"],
    )
    def test_not_protected(self, path):
        assert disk_cleanup.is_protected_path(pathlib.Path(path)) is False

    def test_the_configured_cache_targets_are_all_removable(self):
        for target in disk_cleanup.CACHE_TARGETS:
            assert disk_cleanup.is_protected_path(target) is False

    def test_the_db_backup_tree_is_out_of_scope(self):
        """Retention there is db_backup.py's RETENTION_DAYS, not ours."""
        assert disk_cleanup.is_protected_path(
            pathlib.Path("/home/radon/radon-cloud/backups/db/radon-2026-08-01T090000Z.sql.gz")
        )


class TestStaleWorktreeAdminDirs:
    ADMIN = pathlib.Path("/home/radon/radon/.git/worktrees")

    def test_selects_only_admin_dirs_whose_worktree_is_gone(self):
        entries = [
            (self.ADMIN / "stage.abc1234.AbCdEf", False),
            (self.ADMIN / "stage.def5678.GhIjKl", True),
        ]
        assert disk_cleanup.select_stale_worktree_admin_dirs(entries) == [
            self.ADMIN / "stage.abc1234.AbCdEf"
        ]

    def test_is_a_no_op_when_every_worktree_is_live(self):
        entries = [(self.ADMIN / "stage.abc1234.AbCdEf", True)]
        assert disk_cleanup.select_stale_worktree_admin_dirs(entries) == []


class TestHumanBytes:
    def test_human_bytes(self):
        assert disk_cleanup.human_bytes(0) == "0 B"
        assert disk_cleanup.human_bytes(1536) == "1.5 KiB"
        assert disk_cleanup.human_bytes(12_900_000_000).endswith("GiB")


class TestRunCleanup:
    def test_a_clean_box_reports_ok_with_zero_reclaimed(self):
        outcome = disk_cleanup.run_cleanup(
            categories=(("docker-images", lambda: (0, "nothing to remove")),),
            free_space=lambda: 30_000_000_000,
        )
        assert outcome["state"] == "ok"
        assert outcome["detail"]["reclaimed_bytes"] == 0
        assert outcome["detail"]["errors"] == {}

    def test_one_failing_category_does_not_stop_the_others(self):
        def boom():
            raise RuntimeError("docker is down")

        outcome = disk_cleanup.run_cleanup(
            categories=(
                ("docker-images", boom),
                ("npm-cache", lambda: (8_900_000_000, "removed")),
            ),
            free_space=iter([1_700_000_000, 10_600_000_000]).__next__,
        )
        assert outcome["state"] == "error"
        assert "docker is down" in outcome["detail"]["errors"]["docker-images"]
        assert outcome["detail"]["categories"]["npm-cache"] == 8_900_000_000
        assert outcome["detail"]["reclaimed_bytes"] == 8_900_000_000

    def test_free_space_before_and_after_are_reported(self):
        outcome = disk_cleanup.run_cleanup(
            categories=(("journal", lambda: (900_000_000, "vacuumed")),),
            free_space=iter([1_700_000_000, 2_600_000_000]).__next__,
        )
        detail = outcome["detail"]
        assert detail["free_before_bytes"] == 1_700_000_000
        assert detail["free_after_bytes"] == 2_600_000_000
        assert "journal" in detail["summary"]


class TestUnitFiles:
    def test_timer_fires_on_the_weekend_clear_of_the_db_maintenance_window(self, services_dir):
        text = (services_dir / "radon-disk-cleanup.timer").read_text()
        assert "OnCalendar=Sun *-*-* 03:20:00 UTC" in text
        assert "Persistent=true" in text

    def test_service_runs_the_root_owned_control_plane_copy(self, services_dir):
        text = (services_dir / "radon-disk-cleanup.service").read_text()
        assert "User=root" in text
        # R-341 wrapped ExecStart in flock on the deploy lock, so the literal
        # prefix moved. The ORIGINAL intent of this assertion — root runs the
        # root-owned control-plane copy under -I, never the radon-writable
        # checkout — is unchanged and asserted exactly, as a suffix.
        assert text.count("ExecStart=") == 1
        assert "/usr/bin/python3 -I /usr/local/lib/radon/disk_cleanup.py" in text
        assert "/home/radon/radon/cloud/scripts/disk_cleanup.py" not in text
        assert "EnvironmentFile=" not in text
        assert "Environment=PATH=" in text
