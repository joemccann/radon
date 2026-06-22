"""F6 — Signal alert rules engine.

Behaviours pinned:
  - evaluate_rules() matches a fresh scan row against user rules using the
    declared comparison operator (>, <, >=, <=)
  - a rule whose threshold is not crossed does not fire
  - a rule that fired within the throttle window does not re-fire
  - matches dispatch via the EXISTING watchdog notification helper, called
    with the right arguments (mocked — never hits Pushover/DB)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alerts.evaluate import (
    AlertRule,
    DEFAULT_THROTTLE_SECONDS,
    evaluate_rules,
    rule_matches,
)


def _rule(**overrides) -> AlertRule:
    base = dict(
        id="r1",
        user_id="user_123",
        ticker="AAPL",
        metric="flow_strength",
        op=">",
        threshold=70.0,
        channel="pushover",
        last_fired_at=None,
    )
    base.update(overrides)
    return AlertRule(**base)


NOW = datetime(2026, 6, 21, 15, 0, 0, tzinfo=timezone.utc)


class TestRuleMatches:
    def test_gt_fires_above_threshold(self):
        assert rule_matches(_rule(op=">", threshold=70.0), 80.0) is True

    def test_gt_no_fire_at_threshold(self):
        assert rule_matches(_rule(op=">", threshold=70.0), 70.0) is False

    def test_lt_fires_below_threshold(self):
        assert rule_matches(_rule(op="<", threshold=20.0), 10.0) is True

    def test_lt_no_fire_above(self):
        assert rule_matches(_rule(op="<", threshold=20.0), 25.0) is False

    def test_gte_fires_at_threshold(self):
        assert rule_matches(_rule(op=">=", threshold=70.0), 70.0) is True

    def test_lte_fires_at_threshold(self):
        assert rule_matches(_rule(op="<=", threshold=20.0), 20.0) is True

    def test_lte_no_fire_above(self):
        assert rule_matches(_rule(op="<=", threshold=20.0), 21.0) is False

    def test_none_value_never_matches(self):
        assert rule_matches(_rule(op=">", threshold=1.0), None) is False


class TestEvaluateRules:
    def test_matches_only_relevant_ticker(self):
        rules = [_rule(ticker="AAPL", metric="flow_strength", op=">", threshold=50.0)]
        scan_row = {"ticker": "MSFT", "flow_strength": 90.0}
        matches = evaluate_rules(scan_row, rules, now=NOW)
        assert matches == []

    def test_returns_match_when_metric_crosses(self):
        rules = [_rule(ticker="AAPL", metric="flow_strength", op=">", threshold=50.0)]
        scan_row = {"ticker": "AAPL", "flow_strength": 90.0}
        matches = evaluate_rules(scan_row, rules, now=NOW)
        assert len(matches) == 1
        assert matches[0].rule.id == "r1"
        assert matches[0].value == 90.0

    def test_no_match_when_within_threshold(self):
        rules = [_rule(metric="flow_strength", op=">", threshold=95.0)]
        scan_row = {"ticker": "AAPL", "flow_strength": 90.0}
        assert evaluate_rules(scan_row, rules, now=NOW) == []

    def test_metric_reads_nested_score_alias(self):
        # scanner rows expose the primary signal as "score"
        rules = [_rule(metric="score", op=">=", threshold=70.0)]
        scan_row = {"ticker": "AAPL", "score": 72.0}
        matches = evaluate_rules(scan_row, rules, now=NOW)
        assert len(matches) == 1

    def test_throttle_suppresses_recent_fire(self):
        recent = (NOW - timedelta(seconds=DEFAULT_THROTTLE_SECONDS - 60)).isoformat()
        rules = [_rule(op=">", threshold=50.0, last_fired_at=recent)]
        scan_row = {"ticker": "AAPL", "flow_strength": 90.0}
        assert evaluate_rules(scan_row, rules, now=NOW) == []

    def test_throttle_allows_after_window(self):
        old = (NOW - timedelta(seconds=DEFAULT_THROTTLE_SECONDS + 60)).isoformat()
        rules = [_rule(op=">", threshold=50.0, last_fired_at=old)]
        scan_row = {"ticker": "AAPL", "flow_strength": 90.0}
        assert len(evaluate_rules(scan_row, rules, now=NOW)) == 1


class TestDispatchMatches:
    def test_dispatch_calls_watchdog_notify(self, monkeypatch):
        captured = {}

        def fake_dispatch(outcome):
            captured["outcome"] = outcome

        import alerts.evaluate as ev

        monkeypatch.setattr(ev.notify, "dispatch", fake_dispatch)

        fired = []
        monkeypatch.setattr(ev, "_mark_rule_fired", lambda rule, now: fired.append(rule.id))

        rules = [_rule(op=">", threshold=50.0)]
        scan_row = {"ticker": "AAPL", "flow_strength": 90.0}
        n = ev.evaluate_and_dispatch(scan_row, rules, now=NOW)

        assert n == 1
        assert "outcome" in captured
        outcome = captured["outcome"]
        assert outcome.fired is True
        assert "AAPL" in outcome.message
        assert outcome.severity is not None
        assert fired == ["r1"]

    def test_dispatch_skips_when_no_match(self, monkeypatch):
        import alerts.evaluate as ev

        called = {"n": 0}
        monkeypatch.setattr(ev.notify, "dispatch", lambda outcome: called.__setitem__("n", called["n"] + 1))
        monkeypatch.setattr(ev, "_mark_rule_fired", lambda rule, now: None)

        rules = [_rule(op=">", threshold=99.0)]
        scan_row = {"ticker": "AAPL", "flow_strength": 10.0}
        n = ev.evaluate_and_dispatch(scan_row, rules, now=NOW)

        assert n == 0
        assert called["n"] == 0
