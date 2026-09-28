"""CIP-014 loadfile slice: an API key in the environment (51s on run 36379054426).

Imported so ``--dist loadfile`` can put this class on its own worker of
scripts-gh. The helpers stay in weekend_subscription_only_lib.py, which
pytest does not collect."""

from scripts.tests.weekend_subscription_only_lib import (
    TestAnApiKeyInTheEnvironmentIsIgnored,
    TestTheWrapperNamesEveryBillingReroute,
    test_all_five_wrappers_carry_identical_reroute_lists,
)

__all__ = [
    TestAnApiKeyInTheEnvironmentIsIgnored,
    TestTheWrapperNamesEveryBillingReroute,
    test_all_five_wrappers_carry_identical_reroute_lists,
]
