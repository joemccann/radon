"""Tests for the off-box (Backblaze B2) leg of scripts/db_backup.py.

No live B2 and NO boto3 dependency: the CI cloud-tests job installs only
requirements-dev.txt + cloud/requirements-test.txt, neither of which
carries boto3, and db_backup.py imports it lazily precisely so these tests
never need it. Tests that must reach the transport install a fake ``boto3``
/ ``botocore`` module tree in ``sys.modules`` (see _install_fake_boto3), so
the real lazy-import path in ``_s3_client`` / ``_transfer_config`` is
exercised and the stub client is what actually gets driven.

The local gzip dump is the critical path — every failure mode here must
leave it on disk and still heartbeat.
"""

import gzip
import importlib.util
import pathlib
import sqlite3
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_module():
    name = "db_backup_offbox"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "db_backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses require the module to be present in sys.modules during class
    # creation when loaded via spec_from_file_location.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


db_backup = _load_module()

DAY = 86400

FULL_ARCHIVE_ENV = {
    "RADON_ARCHIVE_S3_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
    "RADON_ARCHIVE_S3_BUCKET": "radon-archive",
    "RADON_ARCHIVE_S3_ACCESS_KEY_ID": "key",
    "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY": "secret",
}


class _StubClient:
    """Minimal S3 surface: list_objects_v2 / upload_file / delete_object."""

    def __init__(self, objects=None, fail_on=None):
        # objects: {key: (size, mtime_epoch_secs)}
        self.objects = dict(objects or {})
        self.fail_on = fail_on
        self.uploaded: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def list_objects_v2(self, **kwargs):
        prefix = kwargs.get("Prefix", "")
        contents = [
            {"Key": k, "Size": v[0], "LastModified": _Stamp(v[1])}
            for k, v in sorted(self.objects.items())
            if k.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def upload_file(self, path, bucket, key, **kwargs):
        if self.fail_on is not None and self.fail_on in key:
            raise RuntimeError("b2 wedged")
        self.uploaded.append((str(path), key))
        self.objects[key] = (pathlib.Path(path).stat().st_size, 0.0)

    def delete_object(self, Bucket, Key):  # noqa: N803 — boto3 kwarg casing
        self.deleted.append(Key)
        self.objects.pop(Key, None)


class _Stamp:
    """Stand-in for the datetime boto3 returns as ``LastModified``."""

    def __init__(self, epoch: float):
        self._epoch = epoch

    def timestamp(self) -> float:
        return self._epoch


def _install_fake_boto3(monkeypatch, client):
    """Put a minimal fake boto3/botocore in sys.modules and return the kwargs
    db_backup passes into them.

    Stubbing beats adding the dependency: cloud/requirements-test.txt is a
    deliberately minimal three-line file, and the module documents its
    import as lazy so unit runs never pull botocore/s3transfer. monkeypatch
    restores sys.modules after each test.
    """
    seen: dict = {}

    class _TransferConfig:
        def __init__(self, **kwargs):
            seen["transfer"] = kwargs

    class _Config:
        def __init__(self, **kwargs):
            seen["botocore_config"] = kwargs

    def _make_client(service, **kwargs):
        seen["service"] = service
        seen["client"] = kwargs
        return client

    boto3_mod = types.ModuleType("boto3")
    s3_mod = types.ModuleType("boto3.s3")
    transfer_mod = types.ModuleType("boto3.s3.transfer")
    botocore_mod = types.ModuleType("botocore")
    botocore_config_mod = types.ModuleType("botocore.config")

    boto3_mod.client = _make_client
    boto3_mod.s3 = s3_mod
    s3_mod.transfer = transfer_mod
    transfer_mod.TransferConfig = _TransferConfig
    botocore_mod.config = botocore_config_mod
    botocore_config_mod.Config = _Config

    for name, module in (
        ("boto3", boto3_mod),
        ("boto3.s3", s3_mod),
        ("boto3.s3.transfer", transfer_mod),
        ("botocore", botocore_mod),
        ("botocore.config", botocore_config_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return seen


class TestNoBoto3AtImport:
    def test_module_import_does_not_pull_boto3(self):
        """The lazy import is load-bearing: the CI cloud-tests job has no
        boto3, so an eager import would break the whole module."""
        before = "boto3" in sys.modules
        _load_module()
        assert ("boto3" in sys.modules) is before


class TestS3ConfigFromEnv:
    def test_reads_archive_keys_with_db_backup_prefix(self):
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))
        assert cfg["bucket"] == "radon-archive"
        assert cfg["endpoint_url"] == "https://s3.us-west-004.backblazeb2.com"
        assert cfg["access_key"] == "key"
        assert cfg["secret_key"] == "secret"
        assert cfg["region"] == "auto"
        assert cfg["prefix"] == "db_backups/"

    def test_host_only_endpoint_gets_https_scheme(self):
        env = dict(FULL_ARCHIVE_ENV, RADON_ARCHIVE_S3_ENDPOINT="s3.us-west-004.backblazeb2.com")
        cfg = db_backup.s3_config_from_env(env)
        assert cfg["endpoint_url"] == "https://s3.us-west-004.backblazeb2.com"

    def test_http_endpoint_is_left_alone(self):
        env = dict(FULL_ARCHIVE_ENV, RADON_ARCHIVE_S3_ENDPOINT="http://localhost:9000")
        assert db_backup.s3_config_from_env(env)["endpoint_url"] == "http://localhost:9000"

    def test_db_backup_overrides_win_over_archive(self):
        env = dict(
            FULL_ARCHIVE_ENV,
            RADON_DB_BACKUP_S3_BUCKET="radon-db",
            RADON_DB_BACKUP_S3_ACCESS_KEY_ID="dbkey",
            RADON_DB_BACKUP_S3_SECRET_ACCESS_KEY="dbsecret",
            RADON_DB_BACKUP_S3_REGION="us-west-004",
        )
        cfg = db_backup.s3_config_from_env(env)
        assert cfg["bucket"] == "radon-db"
        assert cfg["access_key"] == "dbkey"
        assert cfg["secret_key"] == "dbsecret"
        assert cfg["region"] == "us-west-004"

    def test_never_inherits_the_portfolio_snapshot_prefix(self):
        env = dict(FULL_ARCHIVE_ENV, RADON_ARCHIVE_S3_PREFIX="portfolio_snapshots/")
        assert db_backup.s3_config_from_env(env)["prefix"] == "db_backups/"

    def test_prefix_override_gets_a_trailing_slash(self):
        env = dict(FULL_ARCHIVE_ENV, RADON_DB_BACKUP_PREFIX="nightly/db")
        assert db_backup.s3_config_from_env(env)["prefix"] == "nightly/db/"

    @pytest.mark.parametrize(
        "missing",
        [
            "RADON_ARCHIVE_S3_ENDPOINT",
            "RADON_ARCHIVE_S3_BUCKET",
            "RADON_ARCHIVE_S3_ACCESS_KEY_ID",
            "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY",
        ],
    )
    def test_missing_credential_disables_upload(self, missing):
        env = dict(FULL_ARCHIVE_ENV)
        env.pop(missing)
        assert db_backup.s3_config_from_env(env) is None

    def test_blank_credential_disables_upload(self):
        env = dict(FULL_ARCHIVE_ENV, RADON_ARCHIVE_S3_SECRET_ACCESS_KEY="   ")
        assert db_backup.s3_config_from_env(env) is None


class TestObjectKey:
    def test_joins_prefix_and_name(self):
        assert (
            db_backup.object_key_for("db_backups/", "radon-2026-08-27T090000Z.sql.gz")
            == "db_backups/radon-2026-08-27T090000Z.sql.gz"
        )

    def test_adds_missing_separator_and_strips_leading_slash(self):
        assert db_backup.object_key_for("/db_backups", "x.sql.gz") == "db_backups/x.sql.gz"


class TestSelectUploadable:
    def test_skips_objects_already_present_with_the_same_size(self):
        local = [("radon-a.sql.gz", 100)]
        remote = {"db_backups/radon-a.sql.gz": db_backup.RemoteObject(
            key="db_backups/radon-a.sql.gz", size=100, mtime=0.0
        )}
        assert db_backup.select_uploadable(local, remote, "db_backups/") == []

    def test_reuploads_when_remote_size_differs(self):
        local = [("radon-a.sql.gz", 100)]
        remote = {"db_backups/radon-a.sql.gz": db_backup.RemoteObject(
            key="db_backups/radon-a.sql.gz", size=7, mtime=0.0
        )}
        assert db_backup.select_uploadable(local, remote, "db_backups/") == ["radon-a.sql.gz"]

    def test_uploads_everything_missing_newest_first(self):
        local = [
            ("radon-2026-08-01T090000Z.sql.gz", 10),
            ("radon-2026-08-27T090000Z.sql.gz", 10),
            ("radon-2026-08-14T090000Z.sql.gz", 10),
        ]
        assert db_backup.select_uploadable(local, {}, "db_backups/") == [
            "radon-2026-08-27T090000Z.sql.gz",
            "radon-2026-08-14T090000Z.sql.gz",
            "radon-2026-08-01T090000Z.sql.gz",
        ]

    def test_ignores_non_dump_files(self):
        local = [("README.md", 1), (".radon-x.sql.gz.tmp", 1), ("dump.sql", 1)]
        assert db_backup.select_uploadable(local, {}, "db_backups/") == []


class TestRemoteRetention:
    def test_remote_retention_is_longer_than_local(self):
        assert db_backup.REMOTE_RETENTION_DAYS > db_backup.RETENTION_DAYS

    def test_prunes_remote_objects_past_the_remote_window(self):
        now = 1_000_000 * DAY
        entries = [
            ("db_backups/radon-old.sql.gz", now - (db_backup.REMOTE_RETENTION_DAYS + 1) * DAY),
            ("db_backups/radon-new.sql.gz", now - 40 * DAY),
        ]
        assert db_backup.select_remote_prunable(entries, now) == ["db_backups/radon-old.sql.gz"]

    def test_keeps_objects_local_retention_would_have_dropped(self):
        now = 1_000_000 * DAY
        entries = [("db_backups/radon-x.sql.gz", now - 31 * DAY)]
        assert db_backup.select_remote_prunable(entries, now) == []

    def test_never_touches_non_dump_keys(self):
        now = 1_000_000 * DAY
        entries = [("db_backups/notes.txt", now - 5000 * DAY)]
        assert db_backup.select_remote_prunable(entries, now) == []


def _seed_dumps(tmp_path, names, size=32):
    for name in names:
        (tmp_path / name).write_bytes(b"x" * size)
    return tmp_path


class TestSyncOffbox:
    def test_backfills_every_local_dump_on_first_run(self, tmp_path):
        _seed_dumps(tmp_path, [f"radon-2026-08-{d:02d}T090000Z.sql.gz" for d in range(1, 31)])
        client = _StubClient()
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))

        summary = db_backup.sync_offbox(tmp_path, cfg, client=client)

        assert summary["uploaded"] == 30
        assert summary["deferred"] == 0
        assert len(client.uploaded) == 30

    def test_second_run_is_a_no_op(self, tmp_path):
        _seed_dumps(tmp_path, ["radon-a.sql.gz", "radon-b.sql.gz"])
        client = _StubClient()
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))

        db_backup.sync_offbox(tmp_path, cfg, client=client)
        client.uploaded.clear()
        summary = db_backup.sync_offbox(tmp_path, cfg, client=client)

        assert summary["uploaded"] == 0
        assert summary["skipped_present"] == 2
        assert client.uploaded == []

    def test_budget_defers_the_tail_newest_first(self, tmp_path):
        _seed_dumps(tmp_path, ["radon-a.sql.gz", "radon-b.sql.gz", "radon-c.sql.gz"])
        client = _StubClient()
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))
        ticks = iter([0, 0, 10, 999_999])

        summary = db_backup.sync_offbox(
            tmp_path, cfg, client=client, budget_secs=100, clock=lambda: next(ticks)
        )

        assert summary["uploaded"] == 2
        assert summary["deferred"] == 1
        assert [k for _p, k in client.uploaded] == [
            "db_backups/radon-c.sql.gz",
            "db_backups/radon-b.sql.gz",
        ]

    def test_prunes_only_past_the_remote_window(self, tmp_path):
        _seed_dumps(tmp_path, ["radon-a.sql.gz"])
        now = 1_000_000 * DAY
        client = _StubClient(
            {
                "db_backups/radon-ancient.sql.gz": (
                    5,
                    now - (db_backup.REMOTE_RETENTION_DAYS + 5) * DAY,
                ),
                "db_backups/radon-recent.sql.gz": (5, now - 60 * DAY),
            }
        )
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))

        summary = db_backup.sync_offbox(tmp_path, cfg, client=client, now=now)

        assert client.deleted == ["db_backups/radon-ancient.sql.gz"]
        assert summary["remote_pruned"] == 1

    def test_upload_failure_propagates(self, tmp_path):
        _seed_dumps(tmp_path, ["radon-a.sql.gz"])
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))
        with pytest.raises(RuntimeError, match="b2 wedged"):
            db_backup.sync_offbox(tmp_path, cfg, client=_StubClient(fail_on="radon-a"))


def _fake_source_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE journal (id INTEGER PRIMARY KEY, ticker TEXT);")
    conn.execute("INSERT INTO journal VALUES (1, 'AAPL')")
    conn.commit()
    return conn


@pytest.fixture
def backup_env(monkeypatch, tmp_path):
    monkeypatch.setattr(db_backup, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(db_backup, "_open_cloud_db", _fake_source_db)
    for key, value in FULL_ARCHIVE_ENV.items():
        monkeypatch.setenv(key, value)
    return tmp_path


class TestLocalDumpSurvivesUploadFailure:
    def test_dump_is_written_and_kept_when_b2_upload_raises(self, backup_env, monkeypatch):
        _install_fake_boto3(monkeypatch, _StubClient(fail_on="radon-"))

        detail = db_backup.run_backup()

        dumps = sorted(backup_env.glob("*.sql.gz"))
        assert len(dumps) == 1
        assert dumps[0].stat().st_size > 0
        with gzip.open(dumps[0], "rt", encoding="utf-8") as fh:
            assert 'INSERT INTO "journal"' in fh.read()
        assert detail["offbox_error"]
        assert "b2 wedged" in detail["offbox_error"]

    def test_missing_credentials_still_write_the_local_dump(self, backup_env, monkeypatch):
        for key in FULL_ARCHIVE_ENV:
            monkeypatch.delenv(key, raising=False)

        detail = db_backup.run_backup()

        assert len(sorted(backup_env.glob("*.sql.gz"))) == 1
        assert "credential" in detail["offbox_error"].lower()

    def test_upload_failure_heartbeats_error_and_exits_nonzero(self, backup_env, monkeypatch):
        _install_fake_boto3(monkeypatch, _StubClient(fail_on="radon-"))
        beats: list[tuple] = []
        monkeypatch.setattr(
            db_backup,
            "write_service_health",
            lambda state, detail, started_at: beats.append((state, detail)),
        )

        rc = db_backup.main()

        assert rc == 1
        assert len(beats) == 1
        state, detail = beats[0]
        assert state == "error"
        assert "b2 wedged" in detail["summary"]
        # The dump itself succeeded — the summary must still say so.
        assert detail["size_bytes"] > 0
        assert len(sorted(backup_env.glob("*.sql.gz"))) == 1

    def test_successful_upload_heartbeats_ok_with_bytes_uploaded(self, backup_env, monkeypatch):
        client = _StubClient()
        _install_fake_boto3(monkeypatch, client)
        beats: list[tuple] = []
        monkeypatch.setattr(
            db_backup,
            "write_service_health",
            lambda state, detail, started_at: beats.append((state, detail)),
        )

        rc = db_backup.main()

        assert rc == 0
        state, detail = beats[0]
        assert state == "ok"
        assert detail["offbox_uploaded"] == 1
        assert detail["offbox_bytes_uploaded"] == detail["size_bytes"]
        assert "b2 1/1" in detail["summary"]

    def test_transport_is_bounded_and_multipart(self, backup_env, monkeypatch):
        """A wedged B2 must not hang the unit past TimeoutStartSec, so the
        socket timeouts / retries / multipart chunking have to reach boto3."""
        seen = _install_fake_boto3(monkeypatch, _StubClient())
        monkeypatch.setattr(db_backup, "write_service_health", lambda *a, **k: None)

        assert db_backup.main() == 0

        assert seen["service"] == "s3"
        assert seen["client"]["endpoint_url"] == FULL_ARCHIVE_ENV[
            "RADON_ARCHIVE_S3_ENDPOINT"
        ]
        assert seen["botocore_config"]["connect_timeout"] == db_backup.S3_CONNECT_TIMEOUT
        assert seen["botocore_config"]["read_timeout"] == db_backup.S3_READ_TIMEOUT
        assert seen["botocore_config"]["retries"]["max_attempts"] == db_backup.S3_MAX_ATTEMPTS
        assert seen["transfer"]["multipart_chunksize"] == db_backup.MULTIPART_CHUNK_BYTES
        assert seen["transfer"]["max_concurrency"] == db_backup.MULTIPART_CONCURRENCY
