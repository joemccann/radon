"""REL-257 / R-678: Liquid Compute observation replacement must be atomic and report every persistence failure.

Fault injection: seed a valid old observation, inject a failure into its replacement write,
and assert the old row remains queryable; inject HranaHttpError from each persistence step
and assert exactly one liquidcompute error health write plus non-zero exit; a successful
replacement still updates both index and observation records.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.ai_cycle.store import ObservationStore
from scripts.ai_cycle.liquidcompute import persist_ticker, parse_ticker
from scripts.db.hrana_http import HranaHttpError

HASH = "b" * 64
FETCHED = "2026-09-15T12:00:00+00:00"
FIXTURE = Path(__file__).resolve().parents[1] / "ai_cycle" / "fixtures" / "liquidcompute_ticker.json"


def _payload():
    return json.loads(FIXTURE.read_text())


def _make_valid_obs(series_id="h100-us", value=2.6766, fetched_at=FETCHED, raw_hash=HASH):
    """Create a valid observation dict with all required fields."""
    return {
        "indicator_id": "C5",
        "series_id": series_id,
        "source_id": "liquidcompute",
        "period_start": "2026-09-14T00:00:00Z",
        "period_end": "2026-09-14T23:59:59Z",
        "methodology_version": "liquidcompute-ticker-v1",
        "cohort_version": "liquidcompute-us-index-v1",
        "value": value,
        "unit": "usd_per_gpu_per_hr",
        "fetched_at": fetched_at,
        "raw_hash": raw_hash,
        "source_url": "https://liquidcompute.com/api/market/ticker",
        "lineage_group": "liquidcompute",
        "measurement": "observed",
        "metadata": {"vintage": "2026-09-14", "label": "H100"}
    }


class TestLiquidComputeAtomicReplacement:
    """Tests for atomic observation replacement in Liquid Compute writer."""

    def test_upsert_observations_by_identity_is_atomic_on_sqlite(self, tmp_path, request):
        """A failed replacement rolls back the entire batch and releases its transaction."""
        import sqlite3
        from contextlib import closing

        database = tmp_path / "lc.sqlite"
        store = ObservationStore(database)
        request.addfinalizer(store.close)
        store.initialize()
        _store_rows, observations = parse_ticker(_payload(), HASH, FETCHED)
        persist_ticker(store, observations)
        query = "SELECT * FROM ai_cycle_observations ORDER BY identity"
        before = store.connection.execute(query).fetchall()
        assert len(before) == 5

        # Fail inside SQLite, after DELETE and after an earlier row's replacement.
        # This avoids monkeypatching the immutable sqlite3.Cursor implementation.
        store.connection.execute("""
            CREATE TRIGGER reject_h100_replacement
            BEFORE INSERT ON ai_cycle_observations
            WHEN json_extract(NEW.payload, '$.series_id') = 'h100-us'
            BEGIN
                SELECT RAISE(ABORT, 'injected replacement failure');
            END
        """)
        store.connection.commit()
        revised_payload = _payload()
        revised_payload["indices"][1]["value"] = 999.0
        _revised_store, revised_obs = parse_ticker(revised_payload, "d" * 64, "2026-09-15T13:00:00+00:00")
        with pytest.raises(sqlite3.IntegrityError, match="injected replacement failure"):
            store.upsert_observations_by_identity(revised_obs)

        assert not store.connection.in_transaction
        assert store.connection.execute(query).fetchall() == before
        with closing(sqlite3.connect(database)) as observer:
            assert observer.execute(query).fetchall() == before

        # The same connection remains usable, and a retry really replaces rows.
        store.connection.execute("DROP TRIGGER reject_h100_replacement")
        store.connection.commit()
        assert store.upsert_observations_by_identity(revised_obs) == 5
        remaining = store.read_observations(as_of="2026-09-15T16:00:00Z")
        assert len(remaining) == 5
        assert next(row for row in remaining if row["series_id"] == "h100-us")["value"] == 999.0

    def test_upsert_observations_by_identity_atomic_on_hrana(self, tmp_path, monkeypatch):
        """No independently committed DELETE may precede a failed batch."""
        from knowledge import http_db
        store = ObservationStore()
        store.initialize = MagicMock()
        connection = MagicMock()
        connection.execute_transaction.side_effect = HranaHttpError("Injected Hrana INSERT failure")
        monkeypatch.setattr(http_db, "Connection", lambda: connection)
        single_statement = MagicMock(side_effect=AssertionError("untransactional write"))
        monkeypatch.setattr("scripts.db.hrana_http.hrana_execute", single_statement)

        with pytest.raises(HranaHttpError, match="Injected Hrana INSERT failure"):
            store.upsert_observations_by_identity([_make_valid_obs(value=999)])
        connection.execute_transaction.assert_called_once()
        statements = connection.execute_transaction.call_args.args[0]
        assert [sql.split()[0] for sql, _ in statements] == ["DELETE", "INSERT"]
        single_statement.assert_not_called()
        connection.close.assert_called_once()

    def test_persist_ticker_reports_hrana_error_on_observation_failure(self, tmp_path, monkeypatch):
        """RED: HranaHttpError from upsert_observations_by_identity must surface as error health.
        
        persist_ticker should catch exception, write error health, then re-raise.
        """
        store = ObservationStore()
        store.initialize = MagicMock()
        
        # Mock upsert_liquidcompute to succeed
        store.upsert_liquidcompute = MagicMock(return_value=5)
        
        # Mock upsert_observations_by_identity to fail with HranaHttpError
        def failing_upsert(rows):
            raise HranaHttpError("Hrana write failed")
        
        store.upsert_observations_by_identity = failing_upsert
        
        # Mock write_service_health_http to capture calls
        health_calls = []
        def mock_write_health(service, state, started_at, finished_at, error=None, timeout=8):
            health_calls.append({"service": service, "state": state, "error": error})
        
        monkeypatch.setattr("scripts.db.hrana_http.write_service_health_http", mock_write_health)
        
        # Parse and attempt persist
        payload = _payload()
        store_rows, observations = parse_ticker(payload, HASH, FETCHED)
        
        # Should raise HranaHttpError
        with pytest.raises(HranaHttpError, match="Hrana write failed"):
            persist_ticker(store, observations)
        
        # After fix: exactly one liquidcompute error health write
        error_calls = [c for c in health_calls if c["state"] == "error" and c["service"] == "liquidcompute"]
        assert len(error_calls) == 1, "Must write exactly one error health row on persistence failure"
        assert error_calls[0]["error"] is not None

    def test_persist_ticker_reports_hrana_error_on_index_failure(self, tmp_path, monkeypatch):
        """RED: HranaHttpError from upsert_liquidcompute must also surface as error health."""
        store = ObservationStore()
        store.initialize = MagicMock()
        
        # Mock upsert_liquidcompute to fail with HranaHttpError
        def failing_upsert_index(rows):
            raise HranaHttpError("Hrana index write failed")
        
        store.upsert_liquidcompute = failing_upsert_index
        
        health_calls = []
        def mock_write_health(service, state, started_at, finished_at, error=None, timeout=8):
            health_calls.append({"service": service, "state": state, "error": error})
        
        monkeypatch.setattr("scripts.db.hrana_http.write_service_health_http", mock_write_health)
        
        payload = _payload()
        store_rows, observations = parse_ticker(payload, HASH, FETCHED)
        
        with pytest.raises(HranaHttpError, match="Hrana index write failed"):
            persist_ticker(store, observations)
        
        error_calls = [c for c in health_calls if c["state"] == "error" and c["service"] == "liquidcompute"]
        assert len(error_calls) == 1, "Must write exactly one error health row on index persistence failure"
        assert "index write failed" in str(error_calls[0]["error"])

    def test_persist_ticker_success_writes_ok_health(self, tmp_path, monkeypatch):
        """GREEN: Successful persist should not write error health (ok health written by caller)."""
        store = ObservationStore()
        store.initialize = MagicMock()
        store.upsert_liquidcompute = MagicMock(return_value=5)
        store.upsert_observations_by_identity = MagicMock(return_value=5)
        
        health_calls = []
        def mock_write_health(service, state, started_at, finished_at, error=None, timeout=8):
            health_calls.append({"service": service, "state": state, "error": error})
        
        monkeypatch.setattr("scripts.db.hrana_http.write_service_health_http", mock_write_health)
        
        payload = _payload()
        store_rows, observations = parse_ticker(payload, HASH, FETCHED)
        
        result = persist_ticker(store, observations)
        
        assert result == 5
        # persist_ticker itself should not write health on success (caller does)
        error_calls = [c for c in health_calls if c["state"] == "error"]
        assert len(error_calls) == 0, "Should not write error health on success"


class TestFlexBudgetExhaustionVisibility:
    """Tests for making repeated Flex budget exhaustion durable and operator-visible.
    
    R-678 / REL-257 second half: Persist a bounded consecutive-budget/deferred-tail signal
    keyed to the delivery population; after the configured bound write a non-healthy state
    (or an explicitly watchdog-evaluated degraded state), and clear it only after a full catch-up.
    """
    
    def test_consecutive_budget_exhaustion_writes_degraded_state(self, tmp_path, monkeypatch):
        from scripts.tests.test_rel257_flex_budget import BudgetSweep, second_progress_stop

        second_progress_stop(BudgetSweep(monkeypatch, tmp_path))

    def test_single_partial_run_does_not_page(self, tmp_path, monkeypatch):
        from scripts.tests.test_rel257_flex_budget import BudgetSweep, one_progress_stop

        one_progress_stop(BudgetSweep(monkeypatch, tmp_path))

    def test_complete_catchup_clears_degraded_state(self, tmp_path, monkeypatch):
        from scripts.tests.test_rel257_flex_budget import BudgetSweep, full_run_clears_the_streak

        full_run_clears_the_streak(BudgetSweep(monkeypatch, tmp_path))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])