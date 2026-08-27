#!/usr/bin/env python3
"""R-275 / R-276 (REL-103): a registered service cannot die without a row.

Both services are registered for freshness, so a row that NEVER arrives reads
as ordinary staleness rather than as the failure it is — and both carry
multi-day windows, so the silence lasts that long.

  * `ib_reconcile.main()` wrapped its whole body in `try:`/`finally:` with no
    `except`, so any raise between the IB fetch and the health write
    disconnected cleanly and exited with nothing recorded.
  * `fetch_vixcor.run()` had no `try` at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


class TestIbReconcileRecordsItsOwnFailure:
    def test_a_mid_run_failure_writes_an_error_row_and_still_raises(self, monkeypatch):
        import ib_reconcile

        rows: list[dict] = []
        monkeypatch.setattr(
            ib_reconcile, "_record_health",
            lambda state, detail, _r=rows: _r.append({"state": state, "detail": detail}),
        )

        class _Client:
            disconnected = False

            def disconnect(self):
                type(self).disconnected = True

        monkeypatch.setattr(ib_reconcile, "connect_ib", lambda: _Client())
        monkeypatch.setattr(ib_reconcile, "load_trade_log", lambda: [])
        monkeypatch.setattr(ib_reconcile, "load_portfolio_snapshot", lambda: {})

        def boom(_client):
            raise RuntimeError("IB dropped mid-reconcile")

        monkeypatch.setattr(ib_reconcile, "fetch_ib_executions", boom)

        with pytest.raises(RuntimeError):
            ib_reconcile.main()

        assert rows, "the reconciliation spine died with no service_health row"
        assert rows[-1]["state"] == "error"
        assert "IB dropped mid-reconcile" in str(rows[-1]["detail"])
        assert _Client.disconnected, "the finally: no longer disconnects"


class TestVixcorRecordsItsOwnFailure:
    def _stub(self, monkeypatch, mod):
        rows: list[dict] = []
        monkeypatch.setattr(
            mod, "_record_error_health",
            lambda message, error_class, _r=rows: _r.append(
                {"message": message, "class": error_class}
            ),
        )
        monkeypatch.setattr(mod, "load_prior_payload", lambda: None)
        monkeypatch.setattr(mod, "persist_json", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_write_db", lambda *a, **k: None)
        return rows

    def test_a_raising_cboe_fetch_writes_an_error_row(self, monkeypatch):
        import fetch_vixcor

        rows = self._stub(monkeypatch, fetch_vixcor)

        class _Client:
            def fetch_history(self, *a, **k):
                raise ConnectionError("cboe unreachable")

        with pytest.raises(ConnectionError):
            fetch_vixcor.run(client=_Client())
        assert rows and rows[-1]["class"] == "cycle_failed"
        assert "cboe unreachable" in rows[-1]["message"]

    def test_the_no_vix_history_path_writes_an_error_row(self, monkeypatch):
        import fetch_vixcor

        rows = self._stub(monkeypatch, fetch_vixcor)

        class _Client:
            def fetch_history(self, *a, **k):
                return None, None

        with pytest.raises(ValueError):
            fetch_vixcor.run(client=_Client())
        assert rows, "the no-history path exited with no service_health row"

    def test_the_zero_rows_path_writes_an_error_row(self, monkeypatch):
        import fetch_vixcor

        rows = self._stub(monkeypatch, fetch_vixcor)
        monkeypatch.setattr(fetch_vixcor, "parse_index_csv", lambda t: [])
        monkeypatch.setattr(fetch_vixcor, "load_cor3m_rows", lambda: [])
        monkeypatch.setattr(fetch_vixcor, "join_series", lambda a, b: [])

        class _Client:
            def fetch_history(self, *a, **k):
                return "date,close\n", "Mon"

        with pytest.raises(Exception):
            fetch_vixcor.run(client=_Client())
        assert rows, "the zero-rows path exited with no service_health row"


class TestIvrankStaleContractHolds:
    """R-304: the panel's degraded affordance depends on this status surviving.

    Pinned at the WRITER so the contract cannot drift out from under the UI.
    """

    def test_both_feeds_down_still_marks_the_payload_stale_source(self, monkeypatch):
        import fetch_ivrank

        monkeypatch.setattr(
            fetch_ivrank, "load_prior_payload",
            lambda: {"as_of": "2026-08-21", "current": {"iv_rank": 10.5}, "status": "ok"},
        )
        payload = fetch_ivrank._serve_cached("2026-08-27T00:00:00Z", ["ib down", "uw down"])
        assert payload["status"] == fetch_ivrank.STATUS_STALE_SOURCE
        # The restamp is the whole reason the panel needs the flag.
        assert payload["scan_time"] == "2026-08-27T00:00:00Z"
        assert payload["as_of"] == "2026-08-21"

    def test_no_cached_payload_raises_rather_than_serving_nothing(self, monkeypatch):
        import fetch_ivrank

        monkeypatch.setattr(fetch_ivrank, "load_prior_payload", lambda: None)
        with pytest.raises(RuntimeError):
            fetch_ivrank._serve_cached("2026-08-27T00:00:00Z", ["ib down", "uw down"])
