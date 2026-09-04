"""R-379 / REL-132, R-436 / REL-156: a Flex delivery claim does not outlive the
ingest it gated, even when the release fails too.

`claim_flex_delivery` is taken BEFORE any writer runs, and nothing released it.
`upsert_cash_flow_rows` chunks its writes, so a failed `cash_flow_sync` leaves
the earlier chunks committed — and the claim then makes every retry of the same
bytes a no-op `{"ok": True, "outcome": "duplicate"}`. The operator re-drops the
file, or the 08:30 sFTP run re-pulls it, and `flex-pull` heartbeats `ok` over a
permanently half-written `cash_flows`.

A claim is a lease on work in progress, not a record that the work succeeded.

R-436: the release is one best-effort DELETE against the same Turso the writer
just failed on, so the outage that half-writes `cash_flows` also keeps the
claim. The claim therefore carries `status`: `in_progress` from the claim until
every writer has committed, `applied` after. A stale `in_progress` row (older
than one run period) is claimable again; a fresh one is reported as
`in_progress`, never `duplicate`, so the re-pull cannot heartbeat `ok` over it.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flex_delivery_ingest as ingest  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"


class FakeClaims:
    """In-memory stand-in for the `flex_deliveries` table under the R-436 contract.

    `rows` maps digest -> status. A claim wins on an absent row or on an
    `in_progress` row that has aged past one run period (`age_one_period`);
    it loses on `applied` and on a fresh `in_progress`.
    """

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.stale: set[str] = set()
        self.released: list[str] = []
        self.applied: list[str] = []
        self.release_error: Exception | None = None

    def claim(self, digest: str, **_kwargs) -> bool:
        status = self.rows.get(digest)
        if status is None or (status == "in_progress" and digest in self.stale):
            self.rows[digest] = "in_progress"
            self.stale.discard(digest)
            return True
        return False

    def status(self, digest: str) -> str | None:
        return self.rows.get(digest)

    def mark_applied(self, digest: str) -> bool:
        if self.rows.get(digest) != "in_progress":
            return False
        self.rows[digest] = "applied"
        self.applied.append(digest)
        return True

    def release(self, digest: str) -> bool:
        self.released.append(digest)
        if self.release_error is not None:
            raise self.release_error
        return self.rows.pop(digest, None) is not None

    def age_one_period(self) -> None:
        self.stale = {d for d, s in self.rows.items() if s == "in_progress"}


@pytest.fixture
def claims(monkeypatch):
    fake = FakeClaims()
    monkeypatch.setattr(ingest, "claim_flex_delivery", fake.claim)
    monkeypatch.setattr(ingest, "release_flex_delivery", fake.release, raising=False)
    monkeypatch.setattr(ingest, "flex_delivery_status", fake.status, raising=False)
    monkeypatch.setattr(ingest, "mark_flex_delivery_applied", fake.mark_applied, raising=False)
    return fake


@pytest.fixture
def pages(monkeypatch):
    """Every `service_health` row the ingest writes, without touching Hrana."""
    from db import writer

    rows: list[tuple] = []
    monkeypatch.setattr(
        writer,
        "record_service_health",
        lambda service, state, **kw: rows.append((service, state, kw)),
    )
    return rows


class TestFailedIngestReleasesItsClaim:
    def test_a_failed_cash_sync_leaves_the_bytes_retryable(self, monkeypatch, claims, tmp_path):
        """Run 1 fails mid-upsert; run 2 of the SAME bytes must really re-ingest."""
        import cash_flow_sync
        import perf_twr_builder

        twr_calls: list[dict] = []
        monkeypatch.setattr(
            perf_twr_builder,
            "build_and_persist",
            lambda **kwargs: (twr_calls.append(kwargs), {"status": "ok"})[1],
        )

        xml_text = ACTIVITY.read_text()
        path = tmp_path / "activity.xml"
        path.write_text(xml_text)

        monkeypatch.setattr(
            cash_flow_sync, "main", lambda _argv: getattr(cash_flow_sync, "EXIT_WRITE_ERROR", 3)
        )
        first = ingest.ingest_xml(xml_text, source_path=str(path))
        assert first["ok"] is False
        assert twr_calls == []

        monkeypatch.setattr(cash_flow_sync, "main", lambda _argv: 0)
        second = ingest.ingest_xml(xml_text, source_path=str(path))
        assert second.get("outcome") != "duplicate"
        assert second["ok"] is True
        assert twr_calls and twr_calls[0]["persist"] is True

    def test_a_successful_ingest_still_claims_permanently(self, monkeypatch, claims, tmp_path):
        """The R-326 duplicate gate must survive: success is not released."""
        import cash_flow_sync
        import perf_twr_builder

        monkeypatch.setattr(cash_flow_sync, "main", lambda _argv: 0)
        calls: list[dict] = []
        monkeypatch.setattr(
            perf_twr_builder,
            "build_and_persist",
            lambda **kwargs: (calls.append(kwargs), {"status": "ok"})[1],
        )

        xml_text = ACTIVITY.read_text()
        path = tmp_path / "activity.xml"
        path.write_text(xml_text)

        assert ingest.ingest_xml(xml_text, source_path=str(path))["ok"] is True
        again = ingest.ingest_xml(xml_text, source_path=str(path))
        assert again["outcome"] == "duplicate"
        assert len(calls) == 1
        assert claims.released == []

    def test_a_raising_writer_releases_the_claim(self, monkeypatch, claims, tmp_path):
        """An exception is a failed ingest too — the lease has to come back."""
        import cash_flow_sync

        def _boom(_argv):
            raise RuntimeError("turso down")

        monkeypatch.setattr(cash_flow_sync, "main", _boom)
        xml_text = ACTIVITY.read_text()
        path = tmp_path / "activity.xml"
        path.write_text(xml_text)

        with pytest.raises(RuntimeError):
            ingest.ingest_xml(xml_text, source_path=str(path))
        assert claims.rows == {}

    def test_a_failed_trades_rehydrate_releases_the_claim(self, monkeypatch, claims, tmp_path):
        """The trades branch has the same lease, so it needs the same release."""
        import journal_rehydrate

        monkeypatch.setattr(ingest, "classify_flex_xml", lambda _x: ingest.TRADES)
        monkeypatch.setattr(
            ingest, "statement_metadata", lambda _x: {"period_from": None, "period_to": None}
        )
        monkeypatch.setattr(
            journal_rehydrate, "rehydrate", lambda **_k: {"ok": False, "error": "write failed"}
        )
        path = tmp_path / "trades.xml"
        path.write_text("<FlexQueryResponse/>")

        assert ingest.ingest_xml("<FlexQueryResponse/>", source_path=str(path))["ok"] is False
        assert claims.rows == {}


class TestTheClaimCarriesItsStatus:
    """R-436: `in_progress` from the claim, `applied` only after every writer."""

    def test_a_successful_ingest_marks_the_claim_applied_after_every_writer(
        self, monkeypatch, claims, tmp_path
    ):
        import cash_flow_sync
        import perf_twr_builder

        order: list[str] = []
        monkeypatch.setattr(cash_flow_sync, "main", lambda _a: order.append("cash") or 0)
        monkeypatch.setattr(
            perf_twr_builder,
            "build_and_persist",
            lambda **_k: (order.append("twr"), {"status": "ok"})[1],
        )
        original_mark = claims.mark_applied
        monkeypatch.setattr(
            ingest,
            "mark_flex_delivery_applied",
            lambda d: (order.append("applied"), original_mark(d))[1],
        )

        xml_text = ACTIVITY.read_text()
        path = tmp_path / "activity.xml"
        path.write_text(xml_text)

        result = ingest.ingest_xml(xml_text, source_path=str(path))
        assert result["ok"] is True
        assert order == ["cash", "twr", "applied"], (
            "the claim must be marked applied AFTER every writer commits, "
            f"never before; {order}"
        )
        assert claims.rows == {result["content_sha256"]: "applied"}

    def test_b_a_release_that_also_fails_leaves_the_bytes_retryable(
        self, monkeypatch, claims, pages, tmp_path
    ):
        """The R-436 shape: writer raises mid-chunk AND the release raises.

        The claim row survives as `in_progress`. Inside one run period the
        same bytes report `in_progress` (not `ok`, not `duplicate`); once the
        row is stale the next run re-claims it and really re-ingests.
        """
        import cash_flow_sync
        import perf_twr_builder

        twr_calls: list[dict] = []
        monkeypatch.setattr(
            perf_twr_builder,
            "build_and_persist",
            lambda **kwargs: (twr_calls.append(kwargs), {"status": "ok"})[1],
        )

        def _hrana_5xx(_argv):
            raise RuntimeError("Hrana 5xx mid-chunk")

        monkeypatch.setattr(cash_flow_sync, "main", _hrana_5xx)
        claims.release_error = ConnectionError("turso down")

        xml_text = ACTIVITY.read_text()
        path = tmp_path / "activity.xml"
        path.write_text(xml_text)

        with pytest.raises(RuntimeError, match="Hrana 5xx"):
            ingest.ingest_xml(xml_text, source_path=str(path))
        digest = ingest._sha256(xml_text)
        assert claims.released == [digest]
        assert claims.rows == {digest: "in_progress"}

        # Same run period: the lease is still held. Not ok, not duplicate.
        monkeypatch.setattr(cash_flow_sync, "main", lambda _argv: 0)
        held = ingest.ingest_xml(xml_text, source_path=str(path))
        assert held["ok"] is False, (
            "an in_progress claim reported ok=True, so the 08:30 re-pull "
            f"heartbeats fine over a half-applied cash_flows; {held}"
        )
        assert held.get("outcome") == "in_progress"
        assert twr_calls == []

        # One run period later the stale lease is claimable again.
        claims.age_one_period()
        retried = ingest.ingest_xml(xml_text, source_path=str(path))
        assert retried.get("outcome") != "duplicate"
        assert retried["ok"] is True
        assert twr_calls and twr_calls[0]["persist"] is True
        assert claims.rows == {digest: "applied"}

    def test_a_failed_release_pages_on_the_flex_pull_row(
        self, monkeypatch, claims, pages, tmp_path
    ):
        """Best-effort release stays best-effort, but its failure is not silent."""
        import cash_flow_sync

        def _boom(_argv):
            raise RuntimeError("turso down")

        monkeypatch.setattr(cash_flow_sync, "main", _boom)
        claims.release_error = ConnectionError("still down")
        xml_text = ACTIVITY.read_text()
        path = tmp_path / "activity.xml"
        path.write_text(xml_text)

        with pytest.raises(RuntimeError, match="turso down"):
            ingest.ingest_xml(xml_text, source_path=str(path))

        digest = ingest._sha256(xml_text)
        flex_rows = [row for row in pages if row[0] == "flex-pull"]
        assert [(service, state) for service, state, _ in flex_rows] == [("flex-pull", "error")], (
            f"a claim release failure wrote no error row; {pages}"
        )
        error = flex_rows[0][2]["error"]
        assert error["content_sha256"] == digest
        assert "release" in error["message"] and "still down" in error["message"]

    def test_a_failing_page_does_not_mask_the_ingest_failure(
        self, monkeypatch, claims, tmp_path
    ):
        import cash_flow_sync
        from db import writer

        def _boom(_argv):
            raise RuntimeError("turso down")

        def _no_hrana(*_a, **_k):
            raise OSError("hrana unreachable")

        monkeypatch.setattr(cash_flow_sync, "main", _boom)
        monkeypatch.setattr(writer, "record_service_health", _no_hrana)
        claims.release_error = ConnectionError("still down")
        xml_text = ACTIVITY.read_text()
        path = tmp_path / "activity.xml"
        path.write_text(xml_text)

        with pytest.raises(RuntimeError, match="turso down"):
            ingest.ingest_xml(xml_text, source_path=str(path))
        assert claims.rows == {ingest._sha256(xml_text): "in_progress"}


class TestWriterReleasesTheRow:
    def test_release_flex_delivery_deletes_by_digest(self, monkeypatch):
        """The statement must be a DELETE keyed on the content hash."""
        from db import writer

        executed: list[tuple] = []

        class _DB:
            def execute(self, sql, params=None):
                executed.append((sql, params))
                return type("R", (), {"rows_affected": 1})()

            def commit(self):
                return None

        monkeypatch.setattr(writer, "get_db", lambda: _DB())
        assert writer.release_flex_delivery("abc123") is True
        assert len(executed) == 1
        sql, params = executed[0]
        assert "DELETE FROM flex_deliveries" in sql
        assert "content_sha256 = ?" in sql
        assert params == ("abc123",)


class _RecordingDB:
    """Captures every statement; `rowcount` is whatever the test dictates."""

    def __init__(self, rowcount: int = 1, row=None) -> None:
        self.executed: list[tuple] = []
        self.commits = 0
        self.rowcount = rowcount
        self.row = row

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        db = self
        return type(
            "R", (), {"rowcount": db.rowcount, "fetchone": lambda _self: db.row}
        )()

    def commit(self):
        self.commits += 1


NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
NOW_ISO = "2026-08-30T12:00:00Z"
CUTOFF_ISO = "2026-08-30T11:45:00Z"


class TestWriterStatementsPinTheClaimStatus:
    """Pin the emitted statements and their parameters, not sqlite semantics."""

    def test_claim_records_in_progress_and_reclaims_only_a_stale_lease(self, monkeypatch):
        from db import writer

        db = _RecordingDB(rowcount=1)
        monkeypatch.setattr(writer, "get_db", lambda: db)

        assert writer.claim_flex_delivery(
            "abc123",
            classified_as="activity",
            period_from="20260101",
            period_to="20260814",
            source_path="activity.xml",
            now=NOW,
        ) is True
        assert len(db.executed) == 1 and db.commits == 1
        sql, params = db.executed[0]
        assert "INSERT INTO flex_deliveries" in sql
        assert "ON CONFLICT(content_sha256) DO UPDATE" in sql
        assert "flex_deliveries.status = 'in_progress'" in sql
        assert "flex_deliveries.claimed_at < ?" in sql
        assert params == (
            "abc123",
            "activity",
            "20260101",
            "20260814",
            NOW_ISO,
            "activity.xml",
            "in_progress",
            NOW_ISO,
            CUTOFF_ISO,
        ), params

    def test_claim_loses_when_no_row_was_inserted_or_updated(self, monkeypatch):
        from db import writer

        db = _RecordingDB(rowcount=0)
        monkeypatch.setattr(writer, "get_db", lambda: db)
        assert writer.claim_flex_delivery("abc123", classified_as="activity") is False

    def test_the_stale_window_is_shorter_than_the_timer_gap(self):
        """07:30 and 08:30 are the closest two runs; a lease older than the
        window is dead (TimeoutStartSec=120 bounds a run), so the 08:30
        re-pull re-ingests instead of waiting a day."""
        from db import writer

        assert 120 < writer.FLEX_CLAIM_STALE_AFTER_S < 3600

    def test_status_lookup_selects_by_digest(self, monkeypatch):
        from db import writer

        db = _RecordingDB(row=("in_progress",))
        monkeypatch.setattr(writer, "get_db", lambda: db)
        assert writer.flex_delivery_status("abc123") == "in_progress"
        sql, params = db.executed[0]
        assert "SELECT status FROM flex_deliveries" in sql
        assert "content_sha256 = ?" in sql
        assert params == ("abc123",)

    def test_status_lookup_is_none_for_an_unknown_digest(self, monkeypatch):
        from db import writer

        db = _RecordingDB(row=None)
        monkeypatch.setattr(writer, "get_db", lambda: db)
        assert writer.flex_delivery_status("abc123") is None

    def test_mark_applied_updates_only_an_in_progress_row(self, monkeypatch):
        from db import writer

        db = _RecordingDB(rowcount=1)
        monkeypatch.setattr(writer, "get_db", lambda: db)
        assert writer.mark_flex_delivery_applied("abc123") is True
        assert db.commits == 1
        sql, params = db.executed[0]
        assert "UPDATE flex_deliveries" in sql
        assert "status = 'applied'" in sql
        assert "content_sha256 = ?" in sql
        assert "status = 'in_progress'" in sql
        assert params == ("abc123",)


class TestSftpRetryAfterAFailedIngest:
    def test_the_next_scheduled_pull_of_the_same_bytes_is_not_ok(
        self, monkeypatch, claims, tmp_path
    ):
        """07:30 fails mid-upsert; the 08:30 re-pull must not exit 0 with `ok`.

        Drives the REAL `_default_ingest` (no `ingest=` injection) so the claim
        is the only thing standing between the two runs.
        """
        import cash_flow_sync
        import flex_sftp_pull as pull
        from test_flex_sftp_pull import AFTER_FIRST_DELIVERY, FakeSftp, _ssh_config

        heartbeats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: heartbeats.append((state, error)))
        monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
        monkeypatch.setattr(
            cash_flow_sync, "main", lambda _argv: getattr(cash_flow_sync, "EXIT_WRITE_ERROR", 3)
        )

        config = _ssh_config(tmp_path / "ssh_config")
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        payload = ACTIVITY.read_bytes()

        codes = []
        for _ in range(2):
            codes.append(
                pull.run(
                    config=config,
                    inbox=inbox,
                    runner=FakeSftp({"activity.gpg": payload}),
                    decrypt=lambda data, **k: data.decode(),
                    now=AFTER_FIRST_DELIVERY,
                )
            )

        assert codes == [1, 1]
        assert [state for state, _ in heartbeats] == ["error", "error"]

    def test_a_re_pull_over_an_in_progress_claim_is_not_ok(
        self, monkeypatch, claims, tmp_path, capsys
    ):
        """R-436: a fresh `in_progress` lease is neither `ok` nor `duplicate`.

        `now` is after the delivery start so the R-389 stale-remote gate cannot
        be what fails the run; the per-file rejection must name the held claim.
        """
        import cash_flow_sync
        import flex_sftp_pull as pull
        from test_flex_sftp_pull import FakeSftp, _ssh_config

        heartbeats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: heartbeats.append((state, error)))
        monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
        writers: list[str] = []
        monkeypatch.setattr(cash_flow_sync, "main", lambda _argv: writers.append("cash") or 0)

        xml_text = ACTIVITY.read_text()
        claims.rows[ingest._sha256(xml_text)] = "in_progress"

        config = _ssh_config(tmp_path / "ssh_config")
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        code = pull.run(
            config=config,
            inbox=inbox,
            runner=FakeSftp({"activity.gpg": xml_text.encode()}),
            decrypt=lambda data, **k: data.decode(),
            now=datetime(2026, 9, 15, 8, 30, tzinfo=ZoneInfo("America/New_York")),
        )

        assert code == 1
        assert writers == [], "the writers ran over a lease another run still holds"
        assert [state for state, _ in heartbeats] == ["error"], heartbeats
        rejected = capsys.readouterr().err
        assert "ingest_failed" in rejected and "'outcome': 'in_progress'" in rejected, (
            f"the rejection does not name the held claim; {rejected!r}"
        )
