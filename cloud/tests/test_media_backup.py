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


CFG = {
    "endpoint_url": "https://s3.us-west-004.backblazeb2.com",
    "bucket": "radon-archive",
    "access_key": "AK",
    "secret_key": "SK",
    "region": "us-west-004",
    "prefix": "media/",
}

# Production 2026-08-29 10:17Z: botocore raised this class with this text
# on PUT of one PNG; the oneshot then exited 1 with the rest of the planned
# files never attempted (page 02ccb70e).
_CLOSED_MSG = (
    'Connection was closed before we received a valid response from endpoint '
    'URL: "https://s3.us-west-004.backblazeb2.com/radon-archive/media/'
    'c-ahtw9rmk-01-441c6ce148ef.png".'
)


class ConnectionClosedError(Exception):
    """Stand-in for botocore.exceptions.ConnectionClosedError (CI has no boto3)."""


class _FakeS3:
    """list_objects_v2 + upload_file double. No real B2."""

    def __init__(self, *, fail_on=None, fail_times=1):
        self.uploads: list[str] = []
        self.attempts: list[str] = []
        self._fail_on = fail_on
        self._fail_times = fail_times
        self._failed = 0

    def list_objects_v2(self, **kwargs):
        return {"Contents": [], "IsTruncated": False}

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.attempts.append(key)
        if (
            self._fail_on is not None
            and key.endswith(self._fail_on)
            and self._failed < self._fail_times
        ):
            self._failed += 1
            raise ConnectionClosedError(_CLOSED_MSG)
        self.uploads.append(key)


class TestTransientB2UploadRetry:
    """Page 02ccb70e: one ConnectionClosedError must not fail the nightly unit."""

    def test_connection_closed_on_first_png_retries_and_uploads_the_rest(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "c-ahtw9rmk-01-441c6ce148ef.png").write_bytes(b"png-a")
        (tmp_path / "other.png").write_bytes(b"png-b")
        monkeypatch.setattr(media_backup.time, "sleep", lambda _s: None)
        client = _FakeS3(fail_on="c-ahtw9rmk-01-441c6ce148ef.png", fail_times=1)

        summary = media_backup.run_backup(tmp_path, CFG, client=client)

        assert summary["uploaded"] == 2
        assert summary["local_files"] == 2
        assert client.uploads == [
            "media/c-ahtw9rmk-01-441c6ce148ef.png",
            "media/other.png",
        ]
        assert client.attempts.count("media/c-ahtw9rmk-01-441c6ce148ef.png") == 2

    def test_persistent_connection_closed_still_fails_the_unit(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "c-ahtw9rmk-01-441c6ce148ef.png").write_bytes(b"png-a")
        monkeypatch.setattr(media_backup.time, "sleep", lambda _s: None)
        heartbeats = []
        monkeypatch.setattr(
            media_backup,
            "write_service_health",
            lambda state, detail, started_at: heartbeats.append((state, detail)),
        )
        monkeypatch.setattr(media_backup, "s3_config_from_env", lambda: CFG)
        monkeypatch.setattr(
            media_backup,
            "_s3_client",
            lambda _cfg: _FakeS3(
                fail_on="c-ahtw9rmk-01-441c6ce148ef.png", fail_times=99
            ),
        )

        rc = media_backup.main(["--media-dir", str(tmp_path)])

        assert rc == 1
        assert heartbeats and heartbeats[0][0] == "error"
        assert "ConnectionClosedError" in heartbeats[0][1]["summary"]

    def test_non_transient_upload_error_is_not_retried(self, tmp_path, monkeypatch):
        (tmp_path / "a.png").write_bytes(b"png")
        monkeypatch.setattr(media_backup.time, "sleep", lambda _s: None)
        calls = {"n": 0}

        class Denied:
            def list_objects_v2(self, **kwargs):
                return {"Contents": [], "IsTruncated": False}

            def upload_file(self, path, bucket, key, ExtraArgs=None):
                calls["n"] += 1
                raise PermissionError("Access Denied")

        with pytest.raises(PermissionError):
            media_backup.run_backup(tmp_path, CFG, client=Denied())
        assert calls["n"] == 1

    def test_s3_client_uses_bounded_standard_retries(self, monkeypatch):
        import sys
        import types

        seen: dict = {}

        class _Config:
            def __init__(self, **kwargs):
                seen["botocore_config"] = kwargs

        def _make_client(service, **kwargs):
            seen["service"] = service
            seen["client"] = kwargs
            return object()

        boto3_mod = types.ModuleType("boto3")
        botocore_mod = types.ModuleType("botocore")
        botocore_config_mod = types.ModuleType("botocore.config")
        boto3_mod.client = _make_client
        botocore_mod.config = botocore_config_mod
        botocore_config_mod.Config = _Config
        monkeypatch.setitem(sys.modules, "boto3", boto3_mod)
        monkeypatch.setitem(sys.modules, "botocore", botocore_mod)
        monkeypatch.setitem(sys.modules, "botocore.config", botocore_config_mod)

        media_backup._s3_client(CFG)

        assert seen["service"] == "s3"
        cfg = seen["botocore_config"]
        assert cfg["connect_timeout"] == media_backup.S3_CONNECT_TIMEOUT
        assert cfg["read_timeout"] == media_backup.S3_READ_TIMEOUT
        assert cfg["retries"]["max_attempts"] == media_backup.S3_MAX_ATTEMPTS
        assert cfg["retries"]["mode"] == "standard"
