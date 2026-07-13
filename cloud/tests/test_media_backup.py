"""Tests for cloud/scripts/media_backup.py pure helpers (no B2 / Turso)."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_module():
    name = "media_backup"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "media_backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses require the module to be present in sys.modules during class
    # creation when loaded via spec_from_file_location.
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


media_backup = _load_module()


class TestNormalizeEndpoint:
    def test_adds_https_when_missing(self):
        assert (
            media_backup.normalize_endpoint("s3.us-west-004.backblazeb2.com")
            == "https://s3.us-west-004.backblazeb2.com"
        )

    def test_preserves_https(self):
        assert (
            media_backup.normalize_endpoint("https://s3.us-west-004.backblazeb2.com")
            == "https://s3.us-west-004.backblazeb2.com"
        )


class TestS3ConfigFromEnv:
    def test_none_when_empty(self):
        assert media_backup.s3_config_from_env({}) is None

    def test_none_when_partial_archive(self):
        assert (
            media_backup.s3_config_from_env(
                {"RADON_ARCHIVE_S3_ENDPOINT": "https://x", "RADON_ARCHIVE_S3_BUCKET": "b"}
            )
            is None
        )

    def test_reuses_archive_credentials_with_media_prefix(self):
        cfg = media_backup.s3_config_from_env(
            {
                "RADON_ARCHIVE_S3_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
                "RADON_ARCHIVE_S3_BUCKET": "radon-archive",
                "RADON_ARCHIVE_S3_ACCESS_KEY_ID": "AK",
                "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY": "SK",
                "RADON_ARCHIVE_S3_REGION": "us-west-004",
                "RADON_ARCHIVE_S3_PREFIX": "portfolio_snapshots/",
            }
        )
        assert cfg is not None
        assert cfg["bucket"] == "radon-archive"
        assert cfg["prefix"] == "media/"
        assert cfg["region"] == "us-west-004"
        # Portfolio prefix must never leak into media backups.
        assert "portfolio" not in cfg["prefix"]

    def test_media_prefix_override(self):
        cfg = media_backup.s3_config_from_env(
            {
                "RADON_ARCHIVE_S3_ENDPOINT": "https://s3.example",
                "RADON_ARCHIVE_S3_BUCKET": "radon-archive",
                "RADON_ARCHIVE_S3_ACCESS_KEY_ID": "AK",
                "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY": "SK",
                "RADON_MEDIA_BACKUP_PREFIX": "media-backup",
            }
        )
        assert cfg is not None
        assert cfg["prefix"] == "media-backup/"

    def test_media_s3_overrides_win(self):
        cfg = media_backup.s3_config_from_env(
            {
                "RADON_ARCHIVE_S3_ENDPOINT": "https://archive.example",
                "RADON_ARCHIVE_S3_BUCKET": "radon-archive",
                "RADON_ARCHIVE_S3_ACCESS_KEY_ID": "AK1",
                "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY": "SK1",
                "RADON_MEDIA_BACKUP_S3_ENDPOINT": "s3.us-west-004.backblazeb2.com",
                "RADON_MEDIA_BACKUP_S3_BUCKET": "other-bucket",
                "RADON_MEDIA_BACKUP_S3_ACCESS_KEY_ID": "AK2",
                "RADON_MEDIA_BACKUP_S3_SECRET_ACCESS_KEY": "SK2",
                "RADON_MEDIA_BACKUP_S3_REGION": "us-west-004",
            }
        )
        assert cfg is not None
        assert cfg["endpoint_url"] == "https://s3.us-west-004.backblazeb2.com"
        assert cfg["bucket"] == "other-bucket"
        assert cfg["access_key"] == "AK2"
        assert cfg["secret_key"] == "SK2"
        assert cfg["region"] == "us-west-004"
        assert cfg["prefix"] == "media/"


class TestSkipAndKeys:
    def test_skip_hidden_and_tmp(self):
        assert media_backup.should_skip_name(".DS_Store") is True
        assert media_backup.should_skip_name("foo.tmp") is True
        assert media_backup.should_skip_name("foo.part") is True
        assert media_backup.should_skip_name("image.png") is False

    def test_object_key_joins_prefix(self):
        assert media_backup.object_key_for("media/", "a/b.png") == "media/a/b.png"
        assert media_backup.object_key_for("media", "a/b.png") == "media/a/b.png"
        assert media_backup.object_key_for("/media/", "/a/b.png") == "media/a/b.png"


class TestIterLocalFiles:
    def test_walks_and_skips_junk(self, tmp_path):
        (tmp_path / "ok.png").write_bytes(b"png")
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "ok2.jpg").write_bytes(b"jpg")
        (tmp_path / ".hidden").write_bytes(b"x")
        (tmp_path / "wip.tmp").write_bytes(b"x")
        (tmp_path / "nested" / ".cache").mkdir()
        (tmp_path / "nested" / ".cache" / "secret").write_bytes(b"no")

        files = media_backup.iter_local_files(tmp_path)
        rels = [f.relpath for f in files]
        assert rels == ["nested/ok2.jpg", "ok.png"]
        assert files[0].size == 3

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            media_backup.iter_local_files(tmp_path / "nope")


class TestPlanUploads:
    def test_uploads_missing_and_size_mismatch(self):
        local = [
            media_backup.LocalFile("a.png", size=10, mtime_ns=1),
            media_backup.LocalFile("b.png", size=20, mtime_ns=1),
            media_backup.LocalFile("c.png", size=30, mtime_ns=1),
        ]
        remote = {
            "media/a.png": media_backup.RemoteObject("media/a.png", size=10),
            "media/b.png": media_backup.RemoteObject("media/b.png", size=99),
        }
        planned = media_backup.plan_uploads(local, remote, "media/")
        assert [p.relpath for p in planned] == ["b.png", "c.png"]

    def test_all_present_and_matching_is_noop(self):
        local = [media_backup.LocalFile("a.png", size=10, mtime_ns=1)]
        remote = {"media/a.png": media_backup.RemoteObject("media/a.png", size=10)}
        assert media_backup.plan_uploads(local, remote, "media/") == []


class TestRunBackupDryRun:
    def test_dry_run_counts_local_without_client(self, tmp_path):
        (tmp_path / "x.png").write_bytes(b"hello")
        cfg = {
            "endpoint_url": "https://example",
            "bucket": "radon-archive",
            "access_key": "AK",
            "secret_key": "SK",
            "region": "us-west-004",
            "prefix": "media/",
        }
        summary = media_backup.run_backup(tmp_path, cfg, dry_run=True)
        assert summary["dry_run"] is True
        assert summary["local_files"] == 1
        assert summary["uploaded"] == 1
        assert summary["bytes_uploaded"] == 5
        assert summary["remote_objects"] == 0


class TestMainFailClosed:
    def test_main_exits_1_without_credentials(self, monkeypatch, tmp_path):
        monkeypatch.setattr(media_backup, "DEFAULT_MEDIA_DIR", tmp_path)
        monkeypatch.setattr(media_backup.os, "environ", {})
        heartbeats = []

        def capture(state, detail, started_at):
            heartbeats.append((state, detail))

        monkeypatch.setattr(media_backup, "write_service_health", capture)
        rc = media_backup.main(["--media-dir", str(tmp_path)])
        assert rc == 1
        assert heartbeats and heartbeats[0][0] == "error"
        assert "fail-closed" in heartbeats[0][1]["summary"]
