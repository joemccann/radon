"""REL-289 / R-679: TWR gap healing must run even when flex-pull budget is exhausted.

Fault injection: force budget exhaustion during flex-pull and verify that
heal_twr_coverage_gaps is still called for activity statements that were
processed before the budget ran out.
"""

import pytest

from scripts.flex_sftp_pull import heal_twr_coverage_gaps


class TestTWRGapHealBudget:
    """Tests for TWR gap healing under budget pressure."""

    def test_heal_twr_coverage_gaps_called_even_when_budget_spent(self, tmp_path, monkeypatch):
        from scripts.tests.test_rel257_flex_budget import (
            BudgetSweep,
            heal_receives_the_statement_applied_before_the_stop,
        )

        heal_receives_the_statement_applied_before_the_stop(BudgetSweep(monkeypatch, tmp_path))

    def test_heal_twr_coverage_gaps_with_empty_statements(self, tmp_path, monkeypatch):
        """heal_twr_coverage_gaps should return None for empty statements."""
        # Mock the dependencies
        monkeypatch.setattr("scripts.flex_sftp_pull._uncovered_nav_sessions", lambda *a, **k: [])
        
        result = heal_twr_coverage_gaps([], decrypt_fn=lambda x: x, deadline=9999999999)
        assert result is None

    def test_heal_twr_coverage_gaps_called_with_processed_statements(self, tmp_path, monkeypatch):
        from scripts.tests.test_rel257_flex_budget import DAYS, NEWEST, BudgetSweep, delivery_name

        sweep = BudgetSweep(monkeypatch, tmp_path)
        sweep.stop_after_one_file()
        assert sweep.run_once() == 0
        assert sweep.healed == [[delivery_name(NEWEST)]]
        deferred = {delivery_name(day) for day in DAYS if day != NEWEST}
        assert deferred.isdisjoint(sweep.healed[0])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])