"""R-379 / REL-132: a Flex delivery claim does not outlive the ingest it gated.

`claim_flex_delivery` is taken BEFORE any writer runs, and nothing released it.
`upsert_cash_flow_rows` chunks its writes, so a failed `cash_flow_sync` leaves
the earlier chunks committed — and the claim then makes every retry of the same
bytes a no-op `{"ok": True, "outcome": "duplicate"}`. The operator re-drops the
file, or the 08:30 sFTP run re-pulls it, and `flex-pull` heartbeats `ok` over a
permanently half-written `cash_flows`.

A claim is a lease on work in progress, not a record that the work succeeded.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flex_delivery_ingest as ingest  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"


class FakeClaims:
    """In-memory stand-in for the `flex_deliveries` table."""

    def __init__(self) -> None:
        self.rows: set[str] = set()
        self.released: list[str] = []

    def claim(self, digest: str, **_kwargs) -> bool:
        if digest in self.rows:
            return False
        self.rows.add(digest)
        return True

    def release(self, digest: str) -> bool:
        self.released.append(digest)
        existed = digest in self.rows
        self.rows.discard(digest)
        return existed


@pytest.fixture
def claims(monkeypatch):
    fake = FakeClaims()
    monkeypatch.setattr(ingest, "claim_flex_delivery", fake.claim)
    monkeypatch.setattr(ingest, "release_flex_delivery", fake.release, raising=False)
    return fake


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
        assert claims.rows == set()

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
        assert claims.rows == set()


class TestWriterReleasesTheRow:
    def test_release_flex_delivery_deletes_by_digest(self, monkeypatch):
        """The statement must be a DELETE keyed on the content hash."""
        from db import writer

        executed: list[tuple] = []

        class _DB:
            def execute(self, sql, params=None):
                executed.append((sql, params))
                return type("R", (), {"rows_affected": 1})()

        monkeypatch.setattr(writer, "get_db", lambda: _DB())
        assert writer.release_flex_delivery("abc123") is True
        assert len(executed) == 1
        sql, params = executed[0]
        assert "DELETE FROM flex_deliveries" in sql
        assert "content_sha256 = ?" in sql
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
        from test_flex_sftp_pull import FakeSftp, _ssh_config

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
                )
            )

        assert codes == [1, 1]
        assert [state for state, _ in heartbeats] == ["error", "error"]
