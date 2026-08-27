"""Tests for scripts/disk_cleanup.py pure logic (no docker, no host, no Turso).

2026-08-27: the watchdog `root-disk-usage` check paged P1 when the VPS root
filesystem reached 98% (1.7 GiB free on a 75G disk). Nothing on the box ever
pruned the per-SHA ``ghcr.io/joemccann/radon-{node,python}`` image pairs
(~5.8G per deploy), and ``deploy.sh``'s best-effort
``cleanup_staged_release_path`` had leaked seven ``stage.``/``backup.``
worktrees under /home/radon/.radon-releases going back to Jul 11.

Every destructive decision this job makes is a PURE selector tested here, so
the retention rules are provable without a live docker daemon: the running
broker image and the autoheal sidecar can never be selected, and the live
/home/radon/radon checkout can never be selected as an orphaned worktree.
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

HEAD = "a" * 40
NEWER = "b" * 40
NEWEST = "c" * 40
OLD = "d" * 40
OLDEST = "e" * 40


def _image(repo: str, tag: str, created: float, size: int = 1, image_id: str = "sha256:x"):
    return disk_cleanup.Image(
        repository=repo, tag=tag, image_id=image_id, created_at=created, size_bytes=size
    )


def _pair(tag: str, created: float):
    return [
        _image("ghcr.io/joemccann/radon-node", tag, created, 4_690_000_000),
        _image("ghcr.io/joemccann/radon-python", tag, created, 1_110_000_000),
    ]


class TestImageRetention:
    def test_keeps_head_pair_plus_the_newest_n_pairs(self):
        images = (
            _pair(HEAD, 100)
            + _pair(NEWER, 300)
            + _pair(NEWEST, 400)
            + _pair(OLD, 200)
        )
        removed = disk_cleanup.select_prunable_images(images, HEAD, keep_pairs=2)
        removed_tags = {img.tag for img in removed}
        assert removed_tags == {OLD}, (
            "must retain the HEAD pair plus the two newest pairs and remove the rest"
        )
        assert len(removed) == 2, "both halves of a superseded pair go"

    def test_head_pair_survives_even_when_it_is_the_oldest(self):
        images = _pair(HEAD, 1) + _pair(NEWER, 300) + _pair(NEWEST, 400)
        removed = disk_cleanup.select_prunable_images(images, HEAD, keep_pairs=2)
        assert removed == [], "the pair matching /home/radon/radon HEAD is never removable"

    def test_never_returns_the_running_broker_or_autoheal_images(self):
        images = [
            _image("ghcr.io/gnzsnz/ib-gateway", "10.45.1b", 1),
            _image("willfarrell/autoheal", "latest", 1),
        ] + _pair(HEAD, 500) + _pair(OLDEST, 1)
        removed = disk_cleanup.select_prunable_images(images, HEAD, keep_pairs=1)
        repos = {img.repository for img in removed}
        assert "ghcr.io/gnzsnz/ib-gateway" not in repos, "the live broker image is not ours to prune"
        assert "willfarrell/autoheal" not in repos
        assert repos == {
            "ghcr.io/joemccann/radon-node",
            "ghcr.io/joemccann/radon-python",
        }

    def test_never_returns_an_unmanaged_repository(self):
        images = [_image("postgres", "16", 1), _image("caddy", "2", 1)] + _pair(HEAD, 5)
        assert disk_cleanup.select_prunable_images(images, HEAD, keep_pairs=1) == []

    def test_is_a_no_op_below_the_retention_count(self):
        images = _pair(HEAD, 100) + _pair(NEWER, 200)
        assert disk_cleanup.select_prunable_images(images, HEAD, keep_pairs=2) == []

    def test_untagged_images_are_left_to_the_dangling_prune(self):
        images = _pair(HEAD, 400) + [
            _image("ghcr.io/joemccann/radon-node", "<none>", 1),
            _image("ghcr.io/joemccann/radon-python", "", 1),
        ]
        assert disk_cleanup.select_prunable_images(images, HEAD, keep_pairs=1) == []

    def test_an_unresolvable_head_prunes_nothing(self):
        """If HEAD cannot be read, every tag is potentially the running one."""
        images = _pair(HEAD, 100) + _pair(NEWER, 200) + _pair(OLD, 50)
        assert disk_cleanup.select_prunable_images(images, "", keep_pairs=1) == []


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


class TestParsers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("0B", 0),
            ("512B", 512),
            ("989MB", 989_000_000),
            ("4.69GB", 4_690_000_000),
            ("1.1kB", 1_100),
            ("", 0),
            ("N/A", 0),
        ],
    )
    def test_parse_docker_size(self, text, expected):
        assert disk_cleanup.parse_docker_size(text) == expected

    def test_parse_reclaimed_space(self):
        output = "deleted: sha256:abc\n\nTotal reclaimed space: 1.234GB\n"
        assert disk_cleanup.parse_reclaimed_space(output) == 1_234_000_000

    def test_parse_reclaimed_space_when_nothing_was_reclaimed(self):
        assert disk_cleanup.parse_reclaimed_space("Total reclaimed space: 0B\n") == 0
        assert disk_cleanup.parse_reclaimed_space("") == 0

    def test_parse_docker_created(self):
        stamp = disk_cleanup.parse_docker_created("2026-08-20 03:11:22 +0000 UTC")
        assert stamp == pytest.approx(1787195482.0, abs=86400)

    def test_parse_docker_created_is_zero_when_unparseable(self):
        assert disk_cleanup.parse_docker_created("who knows") == 0.0

    def test_parse_image_lines(self):
        line = "ghcr.io/joemccann/radon-node\t" + HEAD + "\tsha256:1\t2026-08-20 03:11:22 +0000 UTC\t4.69GB"
        images = disk_cleanup.parse_image_lines(line + "\nmalformed line\n")
        assert len(images) == 1
        assert images[0].repository == "ghcr.io/joemccann/radon-node"
        assert images[0].tag == HEAD
        assert images[0].size_bytes == 4_690_000_000

    def test_human_bytes(self):
        assert disk_cleanup.human_bytes(0) == "0 B"
        assert disk_cleanup.human_bytes(1536) == "1.5 KiB"
        assert disk_cleanup.human_bytes(12_900_000_000).endswith("GiB")


class TestResolveHeadSha:
    def test_reads_a_symbolic_head_through_a_loose_ref(self, tmp_path):
        git_dir = tmp_path / ".git"
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        (git_dir / "refs" / "heads" / "main").write_text(HEAD + "\n")
        assert disk_cleanup.resolve_head_sha(git_dir) == HEAD

    def test_falls_back_to_packed_refs(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        (git_dir / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            f"{HEAD} refs/heads/main\n"
        )
        assert disk_cleanup.resolve_head_sha(git_dir) == HEAD

    def test_reads_a_detached_head(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text(HEAD + "\n")
        assert disk_cleanup.resolve_head_sha(git_dir) == HEAD

    def test_returns_empty_when_head_is_unreadable_or_bogus(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        assert disk_cleanup.resolve_head_sha(git_dir) == ""
        (git_dir / "HEAD").write_text("ref: refs/heads/gone\n")
        assert disk_cleanup.resolve_head_sha(git_dir) == ""


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
        assert "ExecStart=/usr/bin/python3 -I /usr/local/lib/radon/disk_cleanup.py" in text
        assert "EnvironmentFile=" not in text
        assert "Environment=PATH=" in text
