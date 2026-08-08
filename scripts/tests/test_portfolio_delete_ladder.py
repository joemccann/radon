"""T-046 — delete_portfolio_snapshots_before degradation ladder.

The 2026-07-12 incident hardening (batch fast path → per-key fallback →
wedged-page abort) was dead code under test: every prior test stubbed the
transport with a never-failing sqlite passthrough. These tests drive the
REAL function against a fake Hrana seam that fails on command.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import db.hrana_http as hrana_mod  # noqa: E402
import db.writer as writer  # noqa: E402
from db.hrana_http import HranaHttpError  # noqa: E402


class FakeHranaStore:
    """In-memory portfolio_snapshots keyed by taken_at, with scriptable
    failures for the batched DELETE and per-key DELETE paths."""

    def __init__(self, keys, fail_batch_deletes=0, fail_single_deletes=0):
        self.keys = sorted(keys)
        self.fail_batch_deletes = fail_batch_deletes
        self.fail_single_deletes = fail_single_deletes
        self.batch_attempts = 0
        self.single_attempts = 0

    def query(self, sql, params=(), timeout=None):
        cutoff = params[0]
        matching = [k for k in self.keys if k < cutoff]
        if "LIMIT 1" in sql:
            return [(matching[0],)] if matching else []
        limit = params[1]
        if "COUNT(*)" in sql:
            return [(len(matching[:limit]),)]
        return [(k,) for k in matching[:limit]]

    def execute(self, sql, params=(), timeout=None):
        cutoff = params[0]
        if "IN (" in sql:
            self.batch_attempts += 1
            if self.batch_attempts <= self.fail_batch_deletes:
                raise HranaHttpError("HTTPError: 502 upstream forward failed")
            limit = params[1]
            doomed = [k for k in self.keys if k < cutoff][:limit]
            self.keys = [k for k in self.keys if k not in doomed]
            return []
        # single-key DELETE — params = (taken_at,)
        self.single_attempts += 1
        if self.single_attempts <= self.fail_single_deletes:
            raise HranaHttpError("TimeoutError: timed out")
        self.keys = [k for k in self.keys if k != params[0]]
        return []


@pytest.fixture
def seam(monkeypatch):
    def install(store: FakeHranaStore):
        monkeypatch.setattr(hrana_mod, "hrana_execute", store.execute)
        monkeypatch.setattr(hrana_mod, "hrana_query", store.query)
        return store
    return install


KEYS = [f"2026-07-0{i}T00:00:00Z" for i in range(1, 8)]  # 7 old snapshots
CUTOFF = "2026-08-01T00:00:00Z"


def test_transport_failure_falls_back_to_single_key_and_rearms(seam):
    # Batch DELETE fails persistently at first (both retry attempts x2 rounds),
    # single-key drains a page, then the batch path re-arms and finishes.
    store = seam(FakeHranaStore(KEYS, fail_batch_deletes=2))

    deleted = writer.delete_portfolio_snapshots_before(CUTOFF, batch_size=3)

    assert store.keys == [], f"undeleted keys remain: {store.keys}"
    assert store.single_attempts >= 3, "single-key fallback never ran"
    assert store.batch_attempts >= 3, "batch path never re-armed after a clean page"
    assert deleted == len(KEYS)


def test_fully_wedged_page_aborts_after_one_page(seam):
    store = seam(FakeHranaStore(KEYS, fail_batch_deletes=10**9, fail_single_deletes=10**9))

    deleted = writer.delete_portfolio_snapshots_before(CUTOFF, batch_size=3)

    assert deleted == 0
    # One page of per-key attempts (x2 retry each is fine) — NOT the whole set
    # in a thrash loop; the unit stops and leaves the rest for the next run.
    assert store.single_attempts <= 3 * 2, (
        f"abort did not stop the thrash: {store.single_attempts} single-key attempts"
    )


def test_returned_count_is_rows_actually_removed_not_batch_size(seam):
    # 7 rows, batch_size 200 — the old accounting added batch_size per page
    # and reported 200 deletions for 7 rows (written verbatim into
    # service_health detail by the archive job).
    store = seam(FakeHranaStore(KEYS))

    deleted = writer.delete_portfolio_snapshots_before(CUTOFF, batch_size=200)

    assert store.keys == []
    assert deleted == len(KEYS), f"reported {deleted} deletions for {len(KEYS)} rows"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
