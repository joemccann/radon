"""R-344 / R-372 / REL-125: the backup path cannot resolve to the repo, and an
upload is confirmed before it counts.

R-344: `BACKUP_DIR` and `REPO_ROOT` use `os.environ.get(key, default)`, which
returns `""` for a SET-BUT-EMPTY variable rather than the default — and
`Path("")` is the current directory. `radon-db-backup.service` loads
`/etc/radon/env` wholesale, so a blanked or trailing-edit
`RADON_DB_BACKUP_DIR=` line makes `BACKUP_DIR` resolve to
`WorkingDirectory=/home/radon/radon`; `mkdir(exist_ok=True)` succeeds
silently, the dump lands in the live repo checkout, the retention loop then
unlinks every `*.sql.gz` older than 30 days IN THE REPO, and `sync_offbox`
uploads whatever it finds there to B2.

R-372: `sync_offbox` never verifies the uploaded object landed at the expected
size — it adds the LOCAL `stat().st_size` to `bytes_uploaded` with no
`head_object`. A multipart upload that completes the API call but lands short
is reported as `b2 1/1` with `state = 'ok'`, and `select_uploadable`
re-uploads only on a size DIFFERENCE, so the local original is 30 days from
deletion.
"""

import importlib
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name="db_backup_paths"):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "db_backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestPathsFallBackOnAnEmptyVariable:
    def test_a_set_but_empty_backup_dir_falls_back(self, monkeypatch):
        monkeypatch.setenv("RADON_DB_BACKUP_DIR", "")
        module = _load("db_backup_empty_backup")
        assert str(module.BACKUP_DIR) != "", "Path('') is the current directory"
        assert module.BACKUP_DIR.is_absolute()
        assert module.BACKUP_DIR == pathlib.Path("/home/radon/radon-cloud/backups/db")

    def test_a_set_but_empty_repo_root_falls_back(self, monkeypatch):
        monkeypatch.setenv("RADON_REPO_ROOT", "")
        module = _load("db_backup_empty_repo")
        assert module.REPO_ROOT == pathlib.Path("/home/radon/radon")

    def test_a_whitespace_only_backup_dir_falls_back(self, monkeypatch):
        monkeypatch.setenv("RADON_DB_BACKUP_DIR", "   ")
        module = _load("db_backup_ws_backup")
        assert module.BACKUP_DIR == pathlib.Path("/home/radon/radon-cloud/backups/db")

    def test_a_real_override_is_still_honoured(self, monkeypatch):
        monkeypatch.setenv("RADON_DB_BACKUP_DIR", "/srv/dumps")
        module = _load("db_backup_override")
        assert module.BACKUP_DIR == pathlib.Path("/srv/dumps")


class TestBackupDirIsAssertedBeforeUnlinking:
    def test_a_relative_backup_dir_is_refused(self):
        module = _load("db_backup_assert_rel")
        with pytest.raises(ValueError, match="absolute"):
            module.assert_safe_backup_dir(pathlib.Path("backups"))

    def test_a_backup_dir_inside_the_repo_checkout_is_refused(self):
        module = _load("db_backup_assert_repo")
        with pytest.raises(ValueError, match="repo|checkout"):
            module.assert_safe_backup_dir(module.REPO_ROOT / "backups")

    def test_the_repo_root_itself_is_refused(self):
        module = _load("db_backup_assert_root")
        with pytest.raises(ValueError, match="repo|checkout"):
            module.assert_safe_backup_dir(module.REPO_ROOT)

    def test_the_configured_default_passes(self):
        module = _load("db_backup_assert_ok")
        module.assert_safe_backup_dir(module.BACKUP_DIR)


class _VerifyingStub:
    """S3 surface that can land an object SHORT, as a bad multipart does."""

    def __init__(self, short_by=0):
        self.objects: dict[str, tuple[int, float]] = {}
        self.short_by = short_by
        self.uploaded: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.heads: list[str] = []

    def list_objects_v2(self, **kwargs):
        prefix = kwargs.get("Prefix", "")
        return {
            "Contents": [
                {"Key": k, "Size": v[0], "LastModified": _Stamp(v[1])}
                for k, v in sorted(self.objects.items())
                if k.startswith(prefix)
            ]
        }

    def upload_file(self, path, bucket, key, **kwargs):
        size = pathlib.Path(path).stat().st_size
        self.uploaded.append((str(path), key))
        self.objects[key] = (max(0, size - self.short_by), 0.0)

    def head_object(self, Bucket, Key):  # noqa: N803 — boto3 kwarg casing
        self.heads.append(Key)
        if Key not in self.objects:
            raise RuntimeError("NoSuchKey")
        return {"ContentLength": self.objects[Key][0]}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.deleted.append(Key)
        self.objects.pop(Key, None)


class _Stamp:
    def __init__(self, epoch: float):
        self._epoch = epoch

    def timestamp(self) -> float:
        return self._epoch


class TestUploadIsConfirmedBeforeItCounts:
    def _dump(self, tmp_path, name="radon-20260828T000000Z.sql.gz", size=4096):
        path = tmp_path / name
        path.write_bytes(b"x" * size)
        return path

    def test_a_short_object_raises_instead_of_counting(self, tmp_path):
        """An upload failure already propagates here by design; a short
        landing is an upload failure the API call did not report."""
        module = _load("db_backup_short")
        self._dump(tmp_path)
        client = _VerifyingStub(short_by=1024)

        with pytest.raises(RuntimeError, match="landed at"):
            module.sync_offbox(
                tmp_path, {"bucket": "radon-archive", "prefix": "db"}, client=client
            )
        assert client.heads, "the upload was never confirmed with head_object"

    def test_a_correct_object_still_counts(self, tmp_path):
        module = _load("db_backup_ok")
        path = self._dump(tmp_path)
        client = _VerifyingStub()

        result = module.sync_offbox(
            tmp_path, {"bucket": "radon-archive", "prefix": "db"}, client=client
        )

        assert result["uploaded"] == 1
        assert result["bytes_uploaded"] == path.stat().st_size
        assert client.heads == ["db/radon-20260828T000000Z.sql.gz"]

    def test_a_missing_object_raises(self, tmp_path):
        module = _load("db_backup_missing")
        self._dump(tmp_path)

        class _Vanishing(_VerifyingStub):
            def upload_file(self, path, bucket, key, **kwargs):
                self.uploaded.append((str(path), key))  # never lands

        client = _Vanishing()
        with pytest.raises(RuntimeError, match="NoSuchKey"):
            module.sync_offbox(
                tmp_path, {"bucket": "radon-archive", "prefix": "db"}, client=client
            )
