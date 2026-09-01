"""T-250 / T-257: the Flex delivery claim against a REAL libsql connection.

Every existing reference to the claim monkeypatches the indirection at
`flex_delivery_ingest.claim_flex_delivery`, so `db.writer.claim_flex_delivery`
itself had zero callers in any suite. That seam hid two defects on the same
two lines:

  * the return value read `rows_affected`, which is the JS driver's attribute
    name. `libsql_experimental`'s `Cursor` (pinned 0.0.55) exposes `rowcount`
    only, so `getattr(..., 0)` yielded 0 and the claim was False on FIRST
    sight — every delivery took the "duplicate" branch, `cash_flow_sync`,
    `perf_twr_builder` and `journal_rehydrate` never ran, and the timer
    reported green forever.
  * the INSERT never committed, so the claim row a later run must see did not
    survive the connection.

T-257: the claim is taken BEFORE any writer (correct for idempotency), so a
run that fails mid-ingest must RELEASE it. Otherwise the operator's re-drop of
the same file returns `{"ok": True, "outcome": "duplicate"}` and the
half-written `cash_flows` is unrepairable without a manual DELETE.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import libsql_experimental as libsql
import pytest

from unittest.mock import MagicMock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MIGRATIONS = (
    SCRIPTS / "db" / "migrations" / "0059_flex_deliveries.sql",
    SCRIPTS / "db" / "migrations" / "0063_flex_delivery_claim_status.sql",
)
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY_XML = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"

SHA = "a" * 64
NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_the_driver_under_test_is_the_real_one():
    """This whole file is worthless against a stub.

    A peer module installed a process-wide `sys.modules["libsql_experimental"]`
    MagicMock at import time, so collection order decided whether these tests
    exercised the driver or a mock — and a mock passes `rowcount` and
    `fetchall()` back as MagicMocks, which is exactly the seam T-250 exists to
    close. Fail with a sentence rather than a MagicMock comparison.
    """
    assert not isinstance(getattr(libsql, "connect", None), MagicMock), (
        "libsql_experimental is a stub installed by another test module; these "
        "tests prove nothing about the real driver"
    )


@pytest.fixture
def db_path(tmp_path) -> Path:
    """A file-backed libsql DB carrying migration 0059.

    File-backed, not ``:memory:``, precisely so "does the row survive a fresh
    connection" is answerable — that is the assertion that pins the commit.
    """
    path = tmp_path / "flex_claim.db"
    conn = libsql.connect(str(path))
    for migration in MIGRATIONS:
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    return path


@pytest.fixture
def writer(monkeypatch, db_path):
    """`db.writer` bound to the temp DB, one connection for the whole test."""
    import db.writer as writer_mod

    conn = libsql.connect(str(db_path))
    monkeypatch.setattr(writer_mod, "get_db", lambda: conn)
    return writer_mod


def _rows(db_path: Path) -> list:
    """Read through a FRESH connection — uncommitted writes are invisible."""
    return libsql.connect(str(db_path)).execute(
        "SELECT content_sha256, classified_as, period_from, period_to, source_path "
        "FROM flex_deliveries"
    ).fetchall()


class TestClaimAgainstTheRealDriver:
    def test_the_first_claim_wins(self, writer):
        assert writer.claim_flex_delivery(SHA, classified_as="activity") is True, (
            "the first sight of a delivery did not win the claim, so ingest_xml "
            "takes the 'duplicate' branch for EVERY file and no writer ever runs"
        )

    def test_the_second_claim_loses(self, writer):
        writer.claim_flex_delivery(SHA, classified_as="activity")
        assert writer.claim_flex_delivery(SHA, classified_as="activity") is False

    def test_the_claim_row_survives_a_fresh_connection(self, writer, db_path):
        writer.claim_flex_delivery(
            SHA,
            classified_as="activity",
            period_from="20260101",
            period_to="20260814",
            source_path="/inbox/activity.xml",
        )
        rows = _rows(db_path)
        assert rows == [
            (SHA, "activity", "20260101", "20260814", "/inbox/activity.xml")
        ], f"the claim was never committed, so the next run cannot see it; {rows}"

    def test_release_lets_the_same_file_be_claimed_again(self, writer, db_path):
        assert writer.claim_flex_delivery(SHA, classified_as="activity") is True
        writer.release_flex_delivery(SHA)
        assert _rows(db_path) == []
        assert writer.claim_flex_delivery(SHA, classified_as="activity") is True


def _status(db_path: Path) -> list:
    return libsql.connect(str(db_path)).execute(
        "SELECT content_sha256, status FROM flex_deliveries"
    ).fetchall()


class TestClaimStatusAgainstTheRealDriver:
    """R-436: the lease survives a failed release as `in_progress` and ages out."""

    def test_a_fresh_in_progress_lease_is_not_reclaimable(self, writer, db_path):
        assert writer.claim_flex_delivery(SHA, classified_as="activity", now=NOW) is True
        assert _status(db_path) == [(SHA, "in_progress")]
        assert writer.flex_delivery_status(SHA) == "in_progress"
        later = NOW + timedelta(seconds=writer.FLEX_CLAIM_STALE_AFTER_S - 1)
        assert writer.claim_flex_delivery(SHA, classified_as="activity", now=later) is False

    def test_a_stale_in_progress_lease_is_reclaimable(self, writer, db_path):
        assert writer.claim_flex_delivery(SHA, classified_as="activity", now=NOW) is True
        later = NOW + timedelta(seconds=writer.FLEX_CLAIM_STALE_AFTER_S + 1)
        assert writer.claim_flex_delivery(SHA, classified_as="activity", now=later) is True, (
            "a lease older than one run period stayed unclaimable, so the "
            "outage that failed both the writer and the release wedged the file"
        )
        assert _status(db_path) == [(SHA, "in_progress")]

    def test_an_applied_row_is_never_reclaimable(self, writer, db_path):
        assert writer.claim_flex_delivery(SHA, classified_as="activity", now=NOW) is True
        assert writer.mark_flex_delivery_applied(SHA) is True
        assert _status(db_path) == [(SHA, "applied")]
        assert writer.flex_delivery_status(SHA) == "applied"
        much_later = NOW + timedelta(days=30)
        assert writer.claim_flex_delivery(SHA, classified_as="activity", now=much_later) is False

    def test_mark_applied_is_a_no_op_without_a_lease(self, writer, db_path):
        assert writer.mark_flex_delivery_applied(SHA) is False
        assert _status(db_path) == []

    def test_a_pre_migration_row_defaults_to_applied(self, writer, db_path):
        """Rows claimed before 0063 keep the R-326 duplicate gate."""
        conn = libsql.connect(str(db_path))
        conn.execute(
            "INSERT INTO flex_deliveries (content_sha256, classified_as, ingested_at) "
            "VALUES (?, 'activity', ?)",
            (SHA, "2026-08-01T00:00:00Z"),
        )
        conn.commit()
        assert writer.flex_delivery_status(SHA) == "applied"
        assert writer.claim_flex_delivery(SHA, classified_as="activity", now=NOW) is False


class TestIngestOverTheRealClaim:
    """End to end through `ingest_xml` with the REAL writer, not a stub."""

    @pytest.fixture
    def xml_path(self, tmp_path) -> Path:
        path = tmp_path / "activity.xml"
        path.write_text(ACTIVITY_XML.read_text(), encoding="utf-8")
        return path

    @pytest.fixture
    def runs(self, monkeypatch, writer) -> list:
        import cash_flow_sync
        import perf_twr_builder

        calls: list[str] = []
        monkeypatch.setattr(cash_flow_sync, "main", lambda _a: calls.append("cash") or 0)
        monkeypatch.setattr(
            perf_twr_builder,
            "build_and_persist",
            lambda **_k: (calls.append("twr"), {"status": "ok"})[1],
        )
        return calls

    def test_the_first_ingest_runs_both_writers(self, runs, xml_path):
        import flex_delivery_ingest as ingest

        result = ingest.ingest_path(xml_path)
        assert result["ok"] is True
        assert result.get("outcome") != "duplicate", (
            "the real claim reported the file as already-ingested on first "
            "sight, so the entire Flex ingest is a silent no-op"
        )
        assert runs == ["cash", "twr"]

    def test_the_second_ingest_runs_neither(self, runs, xml_path):
        import flex_delivery_ingest as ingest

        ingest.ingest_path(xml_path)
        second = ingest.ingest_path(xml_path)
        assert second.get("outcome") == "duplicate"
        assert runs == ["cash", "twr"], f"the writers re-ran over an applied file; {runs}"

    def test_a_failed_cash_sync_releases_the_claim_so_a_re_drop_retries(
        self, monkeypatch, writer, xml_path
    ):
        """T-257: exit 3 leaves `cash_flows` half-written; the retry must run."""
        import cash_flow_sync
        import flex_delivery_ingest as ingest
        import perf_twr_builder

        calls: list[str] = []
        exits = [3, 0]
        monkeypatch.setattr(
            cash_flow_sync, "main", lambda _a: calls.append("cash") or exits.pop(0)
        )
        monkeypatch.setattr(
            perf_twr_builder,
            "build_and_persist",
            lambda **_k: (calls.append("twr"), {"status": "ok"})[1],
        )

        first = ingest.ingest_path(xml_path)
        assert first["ok"] is False
        assert first["cash_exit"] == 3

        second = ingest.ingest_path(xml_path)
        assert second.get("outcome") != "duplicate", (
            "the failed run kept its claim, so the operator's re-drop is a "
            "green no-op and the half-written cash_flows is never repaired"
        )
        assert second["ok"] is True
        assert calls == ["cash", "cash", "twr"]

    def test_a_failed_rehydrate_releases_the_claim(self, monkeypatch, writer, tmp_path):
        import flex_delivery_ingest as ingest
        import journal_rehydrate

        trades = FIXTURES / "flex_trade_confirm_sample.xml"
        path = tmp_path / "trades.xml"
        path.write_text(trades.read_text(), encoding="utf-8")

        outcomes = [{"ok": False, "error": "boom"}, {"ok": True, "imported": 1}]
        monkeypatch.setattr(
            journal_rehydrate, "rehydrate", lambda **_k: outcomes.pop(0)
        )

        assert ingest.ingest_path(path)["ok"] is False
        second = ingest.ingest_path(path)
        assert second.get("outcome") != "duplicate"
        assert second["ok"] is True
