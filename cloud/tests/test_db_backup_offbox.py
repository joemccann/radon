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
import os
import pathlib
import sqlite3
import sys
import time
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
        self.attempts: list[str] = []

    def list_objects_v2(self, **kwargs):
        prefix = kwargs.get("Prefix", "")
        contents = [
            {"Key": k, "Size": v[0], "LastModified": _Stamp(v[1])}
            for k, v in sorted(self.objects.items())
            if k.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def upload_file(self, path, bucket, key, **kwargs):
        self.attempts.append(key)
        if self.fail_on is not None and self.fail_on in key:
            raise RuntimeError("b2 wedged")
        self.uploaded.append((str(path), key))
        self.objects[key] = (pathlib.Path(path).stat().st_size, 0.0)

    def head_object(self, Bucket, Key):  # noqa: N803 — boto3 kwarg casing
        """R-372: sync_offbox now CONFIRMS every upload landed at the expected
        size before counting it, so the double needs the head_object surface."""
        if Key not in self.objects:
            raise RuntimeError(f"NoSuchKey: {Key}")
        return {"ContentLength": self.objects[Key][0]}

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


class TestRunOffboxNeverRaises:
    """T-261: `run_offbox`'s docstring is the contract — "Returns
    ``(summary, error)``; NEVER raises." — and nothing asserted it.

    `sync_offbox` one layer down propagates on purpose
    (``test_upload_failure_propagates``); `run_offbox` is the wrapper that
    converts that propagation into a non-fatal ``(None, error)`` so the
    already-landed local dump is not reported as a failed backup. Without
    the try/except, `run_backup` aborts AFTER the dump landed and pruning
    ran, `main`'s outer handler writes "backup failed: ...", and the
    operator is told the dump failed when it did not.
    """

    def test_a_raising_sync_is_returned_as_an_error_string(self, monkeypatch, tmp_path):
        for key, value in FULL_ARCHIVE_ENV.items():
            monkeypatch.setenv(key, value)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("b2 wedged")

        monkeypatch.setattr(db_backup, "sync_offbox", _boom)

        summary, error = db_backup.run_offbox(tmp_path)

        assert summary is None
        assert error == "RuntimeError: b2 wedged"

    def test_a_non_runtime_exception_is_also_swallowed(self, monkeypatch, tmp_path):
        for key, value in FULL_ARCHIVE_ENV.items():
            monkeypatch.setenv(key, value)

        def _boom(*_args, **_kwargs):
            raise ValueError("malformed endpoint")

        monkeypatch.setattr(db_backup, "sync_offbox", _boom)

        summary, error = db_backup.run_offbox(tmp_path)

        assert summary is None
        assert error == "ValueError: malformed endpoint"

    def test_missing_credentials_short_circuit_before_the_transport(
        self, monkeypatch, tmp_path
    ):
        for key in FULL_ARCHIVE_ENV:
            monkeypatch.delenv(key, raising=False)
        for key in (
            "RADON_DB_BACKUP_S3_ENDPOINT",
            "RADON_DB_BACKUP_S3_BUCKET",
            "RADON_DB_BACKUP_S3_ACCESS_KEY_ID",
            "RADON_DB_BACKUP_S3_SECRET_ACCESS_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        called = []
        monkeypatch.setattr(
            db_backup, "sync_offbox", lambda *a, **k: called.append(a) or {}
        )

        summary, error = db_backup.run_offbox(tmp_path)

        assert summary is None
        assert "credentials missing" in error
        assert called == [], "no transport may be attempted without credentials"

    def test_a_success_is_returned_with_no_error(self, monkeypatch, tmp_path):
        for key, value in FULL_ARCHIVE_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            db_backup, "sync_offbox", lambda *a, **k: {"uploaded": 1, "planned": 1}
        )

        summary, error = db_backup.run_offbox(tmp_path)

        assert error is None
        assert summary == {"uploaded": 1, "planned": 1}


class TestARaisingOffboxLegStillReportsTheLandedDump:
    """T-261, the caller half: a wedged off-box leg must heartbeat ``error``
    while the detail still names the dump that DID land, and exit 1."""

    def test_main_heartbeats_error_naming_the_landed_dump(self, backup_env, monkeypatch):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("b2 wedged")

        monkeypatch.setattr(db_backup, "sync_offbox", _boom)
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
        assert detail["offbox_error"] == "RuntimeError: b2 wedged"

        dumps = sorted(backup_env.glob("*.sql.gz"))
        assert len(dumps) == 1
        # The operator must be pointed at the artifact that IS on disk, not
        # told "backup failed" for a dump that landed fine.
        assert detail["path"] == str(dumps[0])
        assert detail["size_bytes"] == dumps[0].stat().st_size > 0
        assert not detail["summary"].startswith("backup failed")


def _empty_source_db():
    """A database whose `sqlite_master` is empty — a credential rotation
    pointing at a fresh DB, or a libsql read that returned no rows."""
    return sqlite3.connect(":memory:")


def _rowless_source_db():
    """Schema present, every table empty."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE journal (id INTEGER PRIMARY KEY, ticker TEXT);")
    conn.commit()
    return conn


class TestAnEmptyDumpIsNotABackup:
    """T-262: `dump_database` reports ``{"tables": 0, "rows": 0}`` without
    complaint and `run_backup` had no plausibility floor between the dump
    and the prune/upload.

    What shipped: a ~120-byte valid gzip is promoted, `select_prunable`
    deletes every real dump past the 30-day window, `sync_offbox` pushes the
    empty artifact to B2, and the heartbeat records ``ok`` with
    "dumped 0 tables / 0 rows -> ... (118 bytes); b2 1/1". Within 30 days
    every local AND remote copy is empty and the health row never said so.
    `lib/vixts_math.py:MIN_SERIES_ROWS` is the same floor for the same
    reason.
    """

    def _seed_expired_dumps(self, backup_dir):
        old = time.time() - 60 * 86400
        names = ["radon-2026-06-01T090000Z.sql.gz", "radon-2026-06-02T090000Z.sql.gz"]
        for name in names:
            path = backup_dir / name
            path.write_bytes(b"real dump")
            os.utime(path, (old, old))
        return names

    def test_a_tableless_dump_neither_prunes_nor_uploads(self, backup_env, monkeypatch):
        expired = self._seed_expired_dumps(backup_env)
        client = _StubClient()
        _install_fake_boto3(monkeypatch, client)
        monkeypatch.setattr(db_backup, "_open_cloud_db", _empty_source_db)

        with pytest.raises(RuntimeError, match="0 tables"):
            db_backup.run_backup()

        for name in expired:
            assert (backup_env / name).exists(), "prune ran on an empty dump"
        assert client.uploaded == [], "an empty dump was pushed off-box"
        assert sorted(p.name for p in backup_env.glob("*.sql.gz")) == sorted(expired), (
            "the empty artifact must not be promoted alongside the real dumps"
        )

    def test_a_rowless_dump_neither_prunes_nor_uploads(self, backup_env, monkeypatch):
        expired = self._seed_expired_dumps(backup_env)
        client = _StubClient()
        _install_fake_boto3(monkeypatch, client)
        monkeypatch.setattr(db_backup, "_open_cloud_db", _rowless_source_db)

        with pytest.raises(RuntimeError, match="0 rows"):
            db_backup.run_backup()

        for name in expired:
            assert (backup_env / name).exists()
        assert client.uploaded == []

    def test_no_in_progress_tmp_file_is_left_behind(self, backup_env, monkeypatch):
        _install_fake_boto3(monkeypatch, _StubClient())
        monkeypatch.setattr(db_backup, "_open_cloud_db", _empty_source_db)

        with pytest.raises(RuntimeError):
            db_backup.run_backup()

        assert list(backup_env.glob(".*.tmp")) == []

    def test_main_heartbeats_error_and_exits_nonzero(self, backup_env, monkeypatch):
        _install_fake_boto3(monkeypatch, _StubClient())
        monkeypatch.setattr(db_backup, "_open_cloud_db", _empty_source_db)
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
        assert state == "error", "an empty dump must never heartbeat ok"
        assert "0 tables" in detail["summary"]

    def test_a_populated_dump_is_still_promoted_pruned_and_uploaded(
        self, backup_env, monkeypatch
    ):
        expired = self._seed_expired_dumps(backup_env)
        client = _StubClient()
        _install_fake_boto3(monkeypatch, client)

        detail = db_backup.run_backup()

        assert detail["tables"] == 1
        assert detail["rows"] == 1
        assert detail["pruned"] == len(expired)
        assert detail["offbox_error"] is None
        # R-445: the off-box leg now runs BEFORE the prune, so the two expired
        # dumps are backfilled and confirmed first, then unlinked. Tonight's
        # dump is uploaded alongside them (was `== 1` when the prune ran first).
        uploaded_names = [pathlib.Path(key).name for _path, key in client.uploaded]
        assert len(uploaded_names) == 1 + len(expired)
        assert detail["path"].endswith(tuple(uploaded_names))
        assert set(expired) <= set(uploaded_names)


# Production 2026-08-29 20:28Z (page 29c8a560): dump of 100 tables / 1.4M
# rows landed, then botocore raised this class with this text on PUT of
# radon-2026-08-29T202254Z.sql.gz (~576 MB). The oneshot exited 1. A
# second run 20:34Z failed the same way. A third run 20:41Z uploaded 3/3.
# Botocore Config retries were already set; they were spent.
_CLOSED_MSG = (
    "Connection was closed before we received a valid response from endpoint "
    'URL: "https://s3.us-west-004.backblazeb2.com/radon-archive/db_backups/'
    'radon-2026-08-29T202254Z.sql.gz".'
)
_FAILED_DUMP = "radon-2026-08-29T202254Z.sql.gz"
_OTHER_DUMP = "radon-2026-08-28T090000Z.sql.gz"


class ConnectionClosedError(Exception):
    """Stand-in for botocore.exceptions.ConnectionClosedError (CI has no boto3)."""


class _ClosedConnectionClient:
    """list/upload/head/delete double. Fail the first N PUTs of one dump."""

    def __init__(self, *, fail_on=None, fail_times=1):
        self.objects: dict[str, tuple[int, float]] = {}
        self.uploaded: list[tuple[str, str]] = []
        self.attempts: list[str] = []
        self.deleted: list[str] = []
        self._fail_on = fail_on
        self._fail_times = fail_times
        self._failed = 0

    def list_objects_v2(self, **kwargs):
        prefix = kwargs.get("Prefix", "")
        return {
            "Contents": [
                {"Key": k, "Size": v[0], "LastModified": _Stamp(v[1])}
                for k, v in sorted(self.objects.items())
                if k.startswith(prefix)
            ],
            "IsTruncated": False,
        }

    def upload_file(self, path, bucket, key, **kwargs):
        self.attempts.append(key)
        if (
            self._fail_on is not None
            and self._fail_on in key
            and self._failed < self._fail_times
        ):
            self._failed += 1
            raise ConnectionClosedError(_CLOSED_MSG)
        self.uploaded.append((str(path), key))
        self.objects[key] = (pathlib.Path(path).stat().st_size, 0.0)

    def head_object(self, Bucket, Key):  # noqa: N803 — boto3 kwarg casing
        if Key not in self.objects:
            raise RuntimeError(f"NoSuchKey: {Key}")
        return {"ContentLength": self.objects[Key][0]}

    def delete_object(self, Bucket, Key):  # noqa: N803 — boto3 kwarg casing
        self.deleted.append(Key)
        self.objects.pop(Key, None)


class TestTransientB2UploadRetry:
    """Page 29c8a560: one ConnectionClosedError must not fail the nightly unit."""

    def test_connection_closed_on_first_dump_retries_and_uploads_the_rest(
        self, tmp_path, monkeypatch
    ):
        _seed_dumps(tmp_path, [_FAILED_DUMP, _OTHER_DUMP])
        monkeypatch.setattr(db_backup.time, "sleep", lambda _s: None)
        client = _ClosedConnectionClient(fail_on=_FAILED_DUMP, fail_times=1)
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))

        summary = db_backup.sync_offbox(tmp_path, cfg, client=client)

        assert summary["uploaded"] == 2
        assert summary["deferred"] == 0
        assert [k for _p, k in client.uploaded] == [
            f"db_backups/{_FAILED_DUMP}",
            f"db_backups/{_OTHER_DUMP}",
        ]
        assert client.attempts.count(f"db_backups/{_FAILED_DUMP}") == 2
        assert client.attempts.count(f"db_backups/{_OTHER_DUMP}") == 1

    def test_persistent_connection_closed_still_fails_the_unit(
        self, backup_env, monkeypatch
    ):
        monkeypatch.setattr(db_backup.time, "sleep", lambda _s: None)
        _install_fake_boto3(
            monkeypatch,
            _ClosedConnectionClient(fail_on="radon-", fail_times=99),
        )
        beats: list[tuple] = []
        monkeypatch.setattr(
            db_backup,
            "write_service_health",
            lambda state, detail, started_at: beats.append((state, detail)),
        )

        rc = db_backup.main()

        assert rc == 1
        assert beats and beats[0][0] == "error"
        assert "ConnectionClosedError" in beats[0][1]["summary"]
        assert len(sorted(backup_env.glob("*.sql.gz"))) == 1

    def test_non_transient_upload_error_is_not_retried(self, tmp_path, monkeypatch):
        _seed_dumps(tmp_path, ["radon-a.sql.gz"])
        monkeypatch.setattr(db_backup.time, "sleep", lambda _s: None)
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))
        client = _StubClient(fail_on="radon-a")

        with pytest.raises(RuntimeError, match="b2 wedged"):
            db_backup.sync_offbox(tmp_path, cfg, client=client)
        assert client.attempts == ["db_backups/radon-a.sql.gz"]
        assert client.uploaded == []


# R-445: 1cb81bc9 cut local retention to 7 days, but `run_backup` pruned every
# `*.sql.gz` past that window BEFORE the off-box leg ran, on mtime alone. B2
# credentials rotate (AccessDenied is non-transient and not retried); each
# night pages `error`, and on night 8 the night-1 dump is unlinked with no
# off-box copy: a permanent hole in the 365-day series.
class AccessDenied(Exception):
    """Shape of a rotated-credential PUT failure: non-transient, not retried."""


class _AccessDeniedClient(_StubClient):
    def upload_file(self, path, bucket, key, **kwargs):
        self.attempts.append(key)
        raise AccessDenied(
            "An error occurred (AccessDenied) when calling the PutObject operation"
        )


def _seed_nightly_dumps(backup_dir, nights=8):
    """Eight nightly dumps aged 1..8 days (less an hour, so the 7-day one sits
    inside the window), OLDEST FIRST; only the oldest is past RETENTION_DAYS."""
    assert nights > db_backup.RETENTION_DAYS
    names = []
    for index in range(nights):
        age_days = nights - index
        name = f"radon-2026-08-{22 + index:02d}T090000Z.sql.gz"
        path = backup_dir / name
        path.write_bytes(b"real dump " + name.encode())
        stamp = time.time() - age_days * DAY + 3600
        os.utime(path, (stamp, stamp))
        names.append(name)
    return names


class TestLocalPruneIsUploadAware:
    def test_eight_nights_of_access_denied_unlink_nothing(self, backup_env, monkeypatch):
        names = _seed_nightly_dumps(backup_env)
        client = _AccessDeniedClient()
        _install_fake_boto3(monkeypatch, client)

        detail = db_backup.run_backup()

        assert "AccessDenied" in detail["offbox_error"]
        assert detail["pruned"] == 0
        for name in names:
            assert (backup_env / name).exists(), f"{name} unlinked with no off-box copy"

    def test_a_dump_b2_already_holds_is_pruned(self, backup_env, monkeypatch):
        names = _seed_nightly_dumps(backup_env)
        oldest = names[0]
        key = db_backup.object_key_for("db_backups/", oldest)
        client = _StubClient(objects={key: ((backup_env / oldest).stat().st_size, 0.0)})
        _install_fake_boto3(monkeypatch, client)

        detail = db_backup.run_backup()

        assert detail["offbox_error"] is None
        assert detail["pruned"] == 1
        assert not (backup_env / oldest).exists()
        for name in names[1:]:
            assert (backup_env / name).exists(), name

    def test_a_dump_confirmed_by_tonights_backfill_is_pruned(self, backup_env, monkeypatch):
        names = _seed_nightly_dumps(backup_env)
        client = _StubClient()
        _install_fake_boto3(monkeypatch, client)

        detail = db_backup.run_backup()

        assert detail["offbox_error"] is None
        # The off-box leg ran FIRST: the expired dump was pushed, confirmed,
        # and only then unlinked.
        assert any(key.endswith(names[0]) for _path, key in client.uploaded), (
            "the expired dump was unlinked before it was backfilled"
        )
        assert detail["pruned"] == 1
        assert not (backup_env / names[0]).exists()

    def test_a_budget_deferred_dump_is_kept(self, backup_env, monkeypatch):
        names = _seed_nightly_dumps(backup_env)
        client = _StubClient()
        _install_fake_boto3(monkeypatch, client)
        real_sync = db_backup.sync_offbox
        monkeypatch.setattr(
            db_backup,
            "sync_offbox",
            lambda backup_dir, cfg, **kw: real_sync(backup_dir, cfg, budget_secs=0, **kw),
        )

        detail = db_backup.run_backup()

        assert detail["offbox_error"] is None
        assert detail["offbox_deferred"] == len(names) + 1
        assert detail["pruned"] == 0
        assert (backup_env / names[0]).exists()

    def test_without_a_b2_config_age_alone_still_prunes(self, backup_env, monkeypatch):
        # No off-box leg configured: the pre-B2 contract (age-only prune)
        # stands, and the missing credentials still heartbeat error.
        names = _seed_nightly_dumps(backup_env)
        for key in FULL_ARCHIVE_ENV:
            monkeypatch.delenv(key, raising=False)

        detail = db_backup.run_backup()

        assert "credential" in detail["offbox_error"].lower()
        assert detail["pruned"] == 1
        assert not (backup_env / names[0]).exists()

    def test_sync_reports_the_names_b2_holds_after_the_run(self, tmp_path):
        _seed_dumps(tmp_path, ["radon-a.sql.gz", "radon-b.sql.gz", "radon-c.sql.gz"])
        cfg = db_backup.s3_config_from_env(dict(FULL_ARCHIVE_ENV))
        client = _StubClient(objects={"db_backups/radon-a.sql.gz": (32, 0.0)})
        ticks = iter([0, 0, 999_999])

        summary = db_backup.sync_offbox(
            tmp_path, cfg, client=client, budget_secs=100, clock=lambda: next(ticks)
        )

        # a: already present; c: uploaded and confirmed tonight; b: deferred.
        assert summary["uploaded"] == 1
        assert summary["deferred"] == 1
        assert summary["confirmed"] == ["radon-a.sql.gz", "radon-c.sql.gz"]

