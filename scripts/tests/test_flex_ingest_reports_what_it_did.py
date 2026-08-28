"""R-327 / R-328 / R-330 / R-360 / REL-116: a Flex ingest reports what it did.

(a) R-327 — the Turso journal write is a per-entry loop with no transaction,
    and an exception partway through returned `imported: 0` even though the
    earlier entries were already committed. A 40-entry file that 502s on entry
    26 left 25 trades in the canonical store while reporting that nothing was
    written, and the operator re-ran against a store they believed untouched.

(b) R-328 — the `FlexSendDisabled` handler emitted `ok` / `file_ingest_only`
    and returned `EXIT_OK`. The scheduled daily unit invokes cash_flow_sync
    with neither `--from-file` nor `--sendrequest`, so it took this branch
    every night, reported healthy and ingested nothing. `cash-flow-sync`
    carries a 25h open window that a nightly green `ok` row can never let fire.

(c) R-330 — `_parse_xml`'s per-row parse is fail-OPEN. A file whose last 60 of
    200 executions carry a corrupted attribute imported 140 fills and returned
    `{'ok': True, 'imported': 140}` with exit 0, and the drops were not
    reflected in `skipped` either.

(d) R-360 — `raise_if_blocked()` ran BEFORE the `allowed` check, so a run that
    provably will never issue a SendRequest was still failed by embargo state.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── (a) R-327: partial journal writes are reported ──────────────────────────

def _executions(n: int) -> str:
    rows = "\n".join(
        f'<Trade symbol="SYM{i}" assetCategory="STK" tradeID="{i}" '
        f'quantity="{i + 1}" tradePrice="1.0" tradeDate="20260810" '
        f'dateTime="20260810;120000" buySell="BUY" ibExecID="e{i}" '
        f'ibOrderID="o{i}" ibCommission="0" />'
        for i in range(n)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<FlexQueryResponse><FlexStatements><FlexStatement>"
        f"<Trades>{rows}</Trades></FlexStatement></FlexStatements>"
        "</FlexQueryResponse>"
    )


class TestPartialJournalWriteIsReported:
    def test_entries_written_before_the_failure_are_counted(self, monkeypatch):
        import journal_rehydrate
        import db.writer as writer

        written: list[str] = []

        def _upsert(trade_id, payload, filled_at=None):
            if len(written) == 25:
                raise RuntimeError("Turso 502 on entry 26")
            written.append(trade_id)

        monkeypatch.setattr(writer, "upsert_journal_entry", _upsert)
        result = journal_rehydrate.rehydrate(
            xml_text=_executions(40), existing={"trades": []}
        )

        assert result["ok"] is False
        assert result["imported"] == 25, (
            "25 trades are already committed to the canonical journal store; "
            f"reporting {result['imported']} sends the operator to re-run "
            "against a store they believe is untouched"
        )
        assert "502" in str(result.get("error"))

    def test_a_clean_write_still_reports_every_entry(self, monkeypatch):
        import journal_rehydrate
        import db.writer as writer

        monkeypatch.setattr(writer, "upsert_journal_entry", MagicMock())
        result = journal_rehydrate.rehydrate(
            xml_text=_executions(40), existing={"trades": []}
        )
        assert result["ok"] is True
        assert result["imported"] == 40


# ── (c) R-330: a dropped <Trade> row fails the ingest closed ────────────────

class TestRowLevelParseFailsClosed:
    def _corrupt_tail(self, total: int, corrupt: int) -> str:
        good = "\n".join(
            f'<Trade symbol="SYM{i}" assetCategory="STK" tradeID="{i}" '
            f'quantity="{i + 1}" tradePrice="1.0" tradeDate="20260810" '
            f'dateTime="20260810;120000" buySell="BUY" ibExecID="e{i}" '
            f'ibOrderID="o{i}" ibCommission="0" />'
            for i in range(total - corrupt)
        )
        bad = "\n".join(
            f'<Trade symbol="SYM{i}" assetCategory="STK" tradeID="{i}" '
            f'quantity="{i + 1}" tradePrice="NOT_A_PRICE" tradeDate="20260810" '
            f'dateTime="20260810;120000" buySell="BUY" ibExecID="e{i}" '
            f'ibOrderID="o{i}" ibCommission="0" />'
            for i in range(total - corrupt, total)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<FlexQueryResponse><FlexStatements><FlexStatement>"
            f"<Trades>{good}\n{bad}</Trades>"
            "</FlexStatement></FlexStatements></FlexQueryResponse>"
        )

    def test_parse_reports_its_own_drop_tally(self):
        from trade_blotter.flex_query import FlexQueryFetcher

        fetcher = FlexQueryFetcher(token="x", query_id="x")
        executions, dropped = fetcher.parse_xml_with_drops(self._corrupt_tail(200, 60))
        assert len(executions) == 140
        assert dropped == 60

    def test_a_dropped_row_fails_the_trades_ingest_closed(self, monkeypatch):
        import journal_rehydrate
        import db.writer as writer

        monkeypatch.setattr(writer, "upsert_journal_entry", MagicMock())
        result = journal_rehydrate.rehydrate(
            xml_text=self._corrupt_tail(200, 60), existing={"trades": []}
        )
        assert result["ok"] is False, (
            "60 fills are missing from the canonical trades store behind a "
            f"green result: {result}"
        )
        assert result.get("dropped_rows") == 60
        assert "60" in str(result.get("error"))

    def test_a_clean_trades_file_still_succeeds(self, monkeypatch):
        import journal_rehydrate
        import db.writer as writer

        monkeypatch.setattr(writer, "upsert_journal_entry", MagicMock())
        result = journal_rehydrate.rehydrate(
            xml_text=self._corrupt_tail(200, 0), existing={"trades": []}
        )
        assert result["ok"] is True
        assert result.get("dropped_rows") == 0


# ── (b) R-328: a no-source no-op is not `ok` ────────────────────────────────

class TestFileIngestOnlyIsNotHealthy:
    def test_the_daily_no_source_run_is_not_reported_ok(self, monkeypatch, capsys):
        import json as _json

        import cash_flow_sync

        # The scheduled unit runs with credentials present and NO source flag.
        monkeypatch.setenv("IB_FLEX_TOKEN", "test-token")
        monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", "1442520")
        code = cash_flow_sync.main(["--no-file"])
        out = capsys.readouterr().out.strip().splitlines()
        status = _json.loads(out[-1])

        assert code != cash_flow_sync.EXIT_OK, (
            "the scheduled unit passes neither --from-file nor --sendrequest, "
            "so this branch runs EVERY night; exiting 0 keeps the 25h "
            "cash-flow-sync staleness window from ever firing"
        )
        assert code == cash_flow_sync.EXIT_FLEX_SEND_DISABLED
        assert status["status"] != "ok"
        assert status["class"] == "file_ingest_only"


# ── (d) R-360: embargo state cannot fail a run that will not send ───────────

class TestEmbargoOnlyGatesARealSendRequest:
    def test_a_no_send_run_is_not_failed_by_embargo_state(self, monkeypatch):
        from utils import flex_send
        from utils import flex_embargo

        def _blocked():
            raise flex_embargo.FlexTokenLocked("1025 embargo live until tomorrow")

        monkeypatch.setattr(flex_embargo, "raise_if_blocked", _blocked)

        with pytest.raises(flex_send.FlexSendDisabled):
            flex_send.assert_sendrequest_permitted(allowed=False)

    def test_a_permitted_run_still_honours_the_embargo(self, monkeypatch):
        from utils import flex_send
        from utils import flex_embargo

        def _blocked():
            raise flex_embargo.FlexTokenLocked("1025 embargo live until tomorrow")

        monkeypatch.setattr(flex_embargo, "raise_if_blocked", _blocked)

        with pytest.raises(flex_embargo.FlexTokenLocked):
            flex_send.assert_sendrequest_permitted(allowed=True)

    def test_a_permitted_unembargoed_run_is_allowed(self, monkeypatch):
        from utils import flex_send
        from utils import flex_embargo

        monkeypatch.setattr(flex_embargo, "raise_if_blocked", lambda: None)
        flex_send.assert_sendrequest_permitted(allowed=True)
