#!/usr/bin/env python3
"""Share-to-X on /regime/cri 502'd because `load_cri` read only host-local disk.

`cri_scan.py --json` mirrors its snapshot to Turso `cri_snapshots` and the page
reads Turso first, so the page showed a CRI while the share script raised
"No CRI cache found" on a host whose `data/cri.json` was absent or stale. The
loader must read Turso too, like `generate_cta_share.load_cta`.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_regime_share as grs  # noqa: E402


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        return _Rows(self.rows)


@pytest.fixture
def empty_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(grs, "SCHEDULED_DIR", tmp_path / "cri_scheduled")
    monkeypatch.setattr(grs, "CACHE_PATH", tmp_path / "cri.json")
    return tmp_path


def _install_db(monkeypatch, db_or_exc):
    def get_db():
        if isinstance(db_or_exc, Exception):
            raise db_or_exc
        return db_or_exc

    monkeypatch.setitem(sys.modules, "db.client", types.SimpleNamespace(get_db=get_db))


def _payload(scan_time: str, date: str = "2026-10-16", score: int = 19) -> dict:
    return {"scan_time": scan_time, "date": date, "cri": {"score": score, "level": "LOW"}}


class TestLoadCriReadsTurso:
    def test_turso_row_is_used_when_disk_has_no_cache(self, empty_disk, monkeypatch):
        payload = _payload("2026-10-16T19:30:03+00:00")
        _install_db(monkeypatch, _FakeDb([(json.dumps(payload),)]))

        assert grs.load_cri() == payload

    def test_no_turso_and_no_disk_still_raises(self, empty_disk, monkeypatch):
        _install_db(monkeypatch, _FakeDb([]))

        with pytest.raises(FileNotFoundError):
            grs.load_cri()

    def test_db_outage_falls_back_to_disk(self, empty_disk, monkeypatch):
        disk = _payload("2026-10-16T18:00:00+00:00", score=22)
        (empty_disk / "cri.json").write_text(json.dumps(disk))
        _install_db(monkeypatch, RuntimeError("hrana down"))

        assert grs.load_cri() == disk

    def test_the_fresher_of_turso_and_disk_wins(self, empty_disk, monkeypatch):
        stale_disk = _payload("2026-10-15T20:00:00+00:00", date="2026-10-15", score=30)
        fresh_db = _payload("2026-10-16T19:30:03+00:00")
        (empty_disk / "cri.json").write_text(json.dumps(stale_disk))
        _install_db(monkeypatch, _FakeDb([(json.dumps(fresh_db),)]))

        assert grs.load_cri() == fresh_db

    def test_target_date_scopes_the_turso_query(self, empty_disk, monkeypatch):
        payload = _payload("2026-10-14T19:30:03+00:00", date="2026-10-14")
        db = _FakeDb([(json.dumps(payload),)])
        _install_db(monkeypatch, db)

        assert grs.load_cri("2026-10-14") == payload
        sql, params = db.queries[-1]
        assert "WHERE date = ?" in sql
        assert params == ("2026-10-14",)
