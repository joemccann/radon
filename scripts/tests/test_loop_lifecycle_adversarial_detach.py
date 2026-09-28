"""CIP-014 loadfile slice: a detached agent child (50.8s on run 36379054426).

Runs beside the reclaim floor, not after it. Each case takes tmp_path."""

from scripts.tests.loop_lifecycle_adversarial_lib import (
    test_a_session_detached_agent_child_does_not_outlive_its_round,
)

__all__ = [
    test_a_session_detached_agent_child_does_not_outlive_its_round,
]
