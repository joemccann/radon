"""R-390 / REL-147: a Flex cash movement is booked once.

The NAV query documented in CLAUDE.md (`1442520`) carries Cash Transactions AND
Transfers in ONE document, so a movement appearing in both sections is the
EXPECTED shape, not an edge case. `_cash_transfer_row` builds
`raw_type = f"Transfer:{xfer_type}:{direction}"` while the CashTransaction row
uses the bare IBKR `type`, and `_disambiguated_id` fingerprints on
`raw_type|amount|date|description` -- so the two hash to DIFFERENT ids and both
are upserted. The `counts` pre-pass mixes both sections, so the shared id is
marked `duplicated` and the divergent-suffix path is GUARANTEED rather than one
row being dropped. External capital flow is doubled and the TWR denominator is
wrong, which is why this is P1 despite sitting in the P2 tranche's file.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cash_flow_sync import parse_cash_transactions  # noqa: E402


def _doc(body: str) -> str:
    return f"<FlexQueryResponse><FlexStatements><FlexStatement>{body}</FlexStatement></FlexStatements></FlexQueryResponse>"


CASH = (
    '<CashTransactions><CashTransaction transactionID="X1" type="Deposits/Withdrawals" '
    'amount="50000" currency="USD" reportDate="2026-08-14" description="WIRE IN"/>'
    "</CashTransactions>"
)
TRANSFER = (
    '<Transfers><Transfer transactionID="X1" assetCategory="CASH" cashTransfer="50000" '
    'type="INTERNAL" direction="IN" date="2026-08-14" description="WIRE IN"/></Transfers>'
)


class TestOneMovementIsOneRow:
    def test_the_shared_transaction_id_yields_exactly_one_row(self):
        rows = parse_cash_transactions(_doc(CASH + TRANSFER))
        assert len(rows) == 1, rows
        assert sum(r["amount"] for r in rows) == 50000.0

    def test_the_surviving_row_keeps_the_raw_transaction_id(self):
        """No sibling to disambiguate against means no hash suffix."""
        rows = parse_cash_transactions(_doc(CASH + TRANSFER))
        assert rows[0]["id"] == "X1", rows

    def test_section_order_does_not_matter(self):
        rows = parse_cash_transactions(_doc(TRANSFER + CASH))
        assert len(rows) == 1, rows
        assert rows[0]["amount"] == 50000.0

    def test_a_cash_transaction_alone_is_unchanged(self):
        rows = parse_cash_transactions(_doc(CASH))
        assert len(rows) == 1
        assert rows[0]["id"] == "X1"

    def test_a_transfer_alone_is_still_booked(self):
        rows = parse_cash_transactions(_doc(TRANSFER))
        assert len(rows) == 1
        assert rows[0]["amount"] == 50000.0


class TestGenuinelyDistinctMovementsSurvive:
    def test_two_transfers_with_different_ids_are_two_rows(self):
        body = (
            '<Transfers>'
            '<Transfer transactionID="A" assetCategory="CASH" cashTransfer="1000" '
            'type="INTERNAL" direction="IN" date="2026-08-14"/>'
            '<Transfer transactionID="B" assetCategory="CASH" cashTransfer="2000" '
            'type="INTERNAL" direction="IN" date="2026-08-14"/>'
            "</Transfers>"
        )
        rows = parse_cash_transactions(_doc(body))
        assert len(rows) == 2
        assert sorted(r["amount"] for r in rows) == [1000.0, 2000.0]

    def test_two_transfers_sharing_an_id_are_still_disambiguated(self):
        body = (
            '<Transfers>'
            '<Transfer transactionID="C" assetCategory="CASH" cashTransfer="1000" '
            'type="INTERNAL" direction="IN" date="2026-08-14"/>'
            '<Transfer transactionID="C" assetCategory="CASH" cashTransfer="2000" '
            'type="INTERNAL" direction="IN" date="2026-08-14"/>'
            "</Transfers>"
        )
        rows = parse_cash_transactions(_doc(body))
        assert len(rows) == 2
        assert len({r["id"] for r in rows}) == 2, rows

    def test_two_cash_transactions_sharing_an_id_are_still_disambiguated(self):
        body = (
            "<CashTransactions>"
            '<CashTransaction transactionID="D" type="Dividends" amount="10" '
            'currency="USD" reportDate="2026-08-14" description="AAPL"/>'
            '<CashTransaction transactionID="D" type="Withholding Tax" amount="-3" '
            'currency="USD" reportDate="2026-08-14" description="AAPL"/>'
            "</CashTransactions>"
        )
        rows = parse_cash_transactions(_doc(body))
        assert len(rows) == 2
        assert len({r["id"] for r in rows}) == 2, rows


class TestConfExamplesAreGone:
    """R-420: the six `.conf.example` files describe a manual, after-hours,
    one-unit-at-a-time cutover and say the drop-ins MUST NOT be installed --
    while bootstrap and the deploy helper now install the real `.conf` for all
    five automatically on the next push."""

    def test_no_example_survives_beside_an_installed_dropin(self):
        cloud = Path(__file__).resolve().parents[2] / "cloud"
        helper = (cloud / "scripts" / "deploy-root-helper.sh").read_text(encoding="utf-8")
        offenders = []
        for example in sorted((cloud / "services").rglob("*.conf.example")):
            sibling = example.with_suffix("")  # drop `.example`
            rel = sibling.relative_to(cloud).as_posix()
            if sibling.is_file() and rel in helper:
                offenders.append(rel)
        assert not offenders, (
            "these examples contradict the installer that now ships their "
            f"sibling automatically: {offenders}"
        )

    def test_a_replaced_dropin_logs_the_digest_it_overwrote(self):
        cloud = Path(__file__).resolve().parents[2] / "cloud"
        helper = (cloud / "scripts" / "deploy-root-helper.sh").read_text(encoding="utf-8")
        body = "\n".join(
            line for line in helper.splitlines() if not line.lstrip().startswith("#")
        )
        start = body.index("refresh_install_file() {")
        end = body.index("\n}\n", start)
        install = body[start:end]
        assert "previous digest" in install or "replacing" in install, (
            "an operator who hand-copied a drop-in to that exact path has it "
            "overwritten with no backup and no journal entry, unlike "
            "install_manifest_units' UNIT_BACKUP_PREFIX snapshot"
        )
