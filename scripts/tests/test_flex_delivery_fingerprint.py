"""R-326 / R-329 / R-359 / REL-115: the Flex delivery fingerprint is real.

R-326: `flex_deliveries` was inert. `content_sha256` was computed and only
echoed into the result dict; the table appeared nowhere in the repo outside
its own migration, so nothing inserted a fingerprint and nothing consulted
one. The migration comment — "content_sha256 is the PK so the same file is
never applied twice" — was false, as were `period_from`/`period_to`, which
nothing populated.

R-359: the classifier validated section presence only, so a statement for the
wrong IBKR account (or an accidentally re-downloaded 365-day statement dropped
into the inbox beside the daily) routed straight into `cash_flow_sync` and
`perf_twr_builder`.

R-329: the duplicate-`transactionID` disambiguator derived its synthetic id
from DOCUMENT POSITION, so the id of a given economic row was unstable across
statement regenerations. `upsert_cash_flow_rows` is insert/update-only with no
delete pass, so a reissued statement with one of a duplicate trio dropped left
the orphan behind as a phantom row and overstated the cash-flow total.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.flex_classify import (  # noqa: E402
    ACTIVITY,
    FlexClassifyError,
    classify_flex_xml,
    statement_metadata,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY_XML = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"


class TestStatementMetadata:
    def test_account_and_period_are_extracted(self):
        meta = statement_metadata(ACTIVITY_XML.read_text())
        assert meta["account_id"] == "U0000000"
        assert meta["period_from"] == "20260101"
        assert meta["period_to"] == "20260814"

    def test_a_wrong_account_file_is_refused(self, monkeypatch):
        monkeypatch.setenv("IB_FLEX_ACCOUNT_ID", "U9999999")
        with pytest.raises(FlexClassifyError, match="account_mismatch"):
            classify_flex_xml(ACTIVITY_XML.read_text())

    def test_the_configured_account_still_classifies(self, monkeypatch):
        monkeypatch.setenv("IB_FLEX_ACCOUNT_ID", "U0000000")
        assert classify_flex_xml(ACTIVITY_XML.read_text()) == ACTIVITY

    def test_an_unset_account_does_not_gate(self, monkeypatch):
        """No configured account -> no check. Fail-closed needs a value to close on."""
        monkeypatch.delenv("IB_FLEX_ACCOUNT_ID", raising=False)
        assert classify_flex_xml(ACTIVITY_XML.read_text()) == ACTIVITY


class TestDeliveryFingerprintIsConsulted:
    def test_the_same_file_twice_runs_the_writers_once(self, monkeypatch, tmp_path):
        import cash_flow_sync
        import flex_delivery_ingest as ingest
        import perf_twr_builder

        claimed: list[str] = []
        seen: set[str] = set()

        def _claim(digest, **kwargs):
            if digest in seen:
                return False
            seen.add(digest)
            claimed.append(digest)
            return True

        monkeypatch.setattr(ingest, "claim_flex_delivery", _claim)
        runs: list[str] = []
        monkeypatch.setattr(cash_flow_sync, "main", lambda _a: runs.append("cash") or 0)
        monkeypatch.setattr(
            perf_twr_builder, "build_and_persist",
            lambda **_k: (runs.append("twr"), {"status": "ok"})[1],
        )

        path = tmp_path / "activity.xml"
        path.write_text(ACTIVITY_XML.read_text(), encoding="utf-8")

        first = ingest.ingest_path(path)
        second = ingest.ingest_path(path)

        assert runs == ["cash", "twr"], (
            f"the second ingest re-ran the writers over an already-applied file; {runs}"
        )
        assert first["ok"] is True
        assert second.get("outcome") == "duplicate"
        assert len(claimed) == 1

    def test_the_claim_carries_the_statement_period(self, monkeypatch, tmp_path):
        import cash_flow_sync
        import flex_delivery_ingest as ingest
        import perf_twr_builder

        captured: dict = {}

        def _claim(digest, **kwargs):
            captured.update(kwargs)
            return True

        monkeypatch.setattr(ingest, "claim_flex_delivery", _claim)
        monkeypatch.setattr(cash_flow_sync, "main", lambda _a: 0)
        monkeypatch.setattr(
            perf_twr_builder, "build_and_persist", lambda **_k: {"status": "ok"}
        )
        path = tmp_path / "activity.xml"
        path.write_text(ACTIVITY_XML.read_text(), encoding="utf-8")
        ingest.ingest_path(path)

        assert captured["period_from"] == "20260101"
        assert captured["period_to"] == "20260814"
        assert captured["classified_as"] == ACTIVITY
        assert captured["source_path"].endswith("activity.xml")

    def test_the_claim_runs_before_any_writer(self, monkeypatch, tmp_path):
        import cash_flow_sync
        import flex_delivery_ingest as ingest
        import perf_twr_builder

        order: list[str] = []
        monkeypatch.setattr(
            ingest, "claim_flex_delivery", lambda d, **k: order.append("claim") or True
        )
        monkeypatch.setattr(cash_flow_sync, "main", lambda _a: order.append("cash") or 0)
        monkeypatch.setattr(
            perf_twr_builder, "build_and_persist",
            lambda **_k: (order.append("twr"), {"status": "ok"})[1],
        )
        path = tmp_path / "activity.xml"
        path.write_text(ACTIVITY_XML.read_text(), encoding="utf-8")
        ingest.ingest_path(path)
        assert order == ["claim", "cash", "twr"]


_DUP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="Equity Summary in Base" type="AF">
<FlexStatements count="1">
<FlexStatement accountId="U0000000" fromDate="20260101" toDate="20260814" period="YearToDate">
<EquitySummaryInBase>
<EquitySummaryByReportDateInBase reportDate="20260101" total="1" />
</EquitySummaryInBase>
<CashTransactions>
{rows}
</CashTransactions>
<Transfers></Transfers>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>
"""

_ROW = (
    '<CashTransaction currency="USD" description="{desc}" amount="{amt}" '
    'type="Deposits/Withdrawals" transactionID="T1" reportDate="{date}" />'
)


def _statement(rows):
    return _DUP_XML.format(rows="\n".join(_ROW.format(**r) for r in rows))


class TestDuplicateIdIsContentKeyed:
    """R-329: the same economic row must keep its id across a reissue."""

    TRIO = [
        {"desc": "alpha", "amt": "100", "date": "20260102"},
        {"desc": "bravo", "amt": "200", "date": "20260103"},
        {"desc": "charlie", "amt": "300", "date": "20260104"},
    ]

    def _ids(self, rows):
        import cash_flow_sync

        parsed = cash_flow_sync.parse_cash_transactions(_statement(rows))
        return {r["description"]: r["id"] for r in parsed}

    def test_ids_are_stable_when_a_sibling_is_dropped(self):
        full = self._ids(self.TRIO)
        corrected = self._ids([self.TRIO[0], self.TRIO[2]])
        assert corrected["charlie"] == full["charlie"], (
            "dropping 'bravo' shifted 'charlie' onto 'bravo''s ordinal id, so "
            "the upsert leaves the old charlie row behind as a phantom and the "
            f"cash-flow total is overstated; full={full} corrected={corrected}"
        )
        assert corrected["alpha"] == full["alpha"]

    def test_ids_are_stable_when_a_row_is_inserted_ahead(self):
        full = self._ids(self.TRIO)
        reordered = self._ids([
            {"desc": "zulu", "amt": "50", "date": "20260101"}, *self.TRIO,
        ])
        for desc in ("alpha", "bravo", "charlie"):
            assert reordered[desc] == full[desc], desc

    def test_ids_are_stable_under_reordering(self):
        full = self._ids(self.TRIO)
        shuffled = self._ids([self.TRIO[2], self.TRIO[0], self.TRIO[1]])
        assert shuffled == full

    def test_distinct_rows_still_get_distinct_ids(self):
        ids = self._ids(self.TRIO)
        assert len(set(ids.values())) == 3

    def test_a_lone_transaction_id_is_left_unsuffixed(self):
        """The common case must keep the raw IBKR id, not gain a hash."""
        ids = self._ids([self.TRIO[0]])
        assert ids["alpha"] == "T1"

    def test_two_genuinely_identical_rows_collapse_rather_than_double_count(self):
        """Byte-identical duplicates are the same economics; one id, one row."""
        ids = self._ids([self.TRIO[0], self.TRIO[0]])
        assert len(set(ids.values())) == 1
