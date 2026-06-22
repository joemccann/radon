"""F6 — signal alert rules engine.

Diffs a fresh scan row against user-defined alert rules and dispatches matches
through the existing watchdog notification helper. Public surface lives in
``alerts.evaluate``.
"""
from .evaluate import (
    AlertRule,
    RuleMatch,
    evaluate_and_dispatch,
    evaluate_rules,
    load_rules_for_ticker,
    rule_matches,
    run_alerts_for_results,
)

__all__ = [
    "AlertRule",
    "RuleMatch",
    "evaluate_and_dispatch",
    "evaluate_rules",
    "load_rules_for_ticker",
    "rule_matches",
    "run_alerts_for_results",
]
