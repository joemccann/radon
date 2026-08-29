"""Every e2e spec is either curated in CI or explicitly held out (T-271).

The `e2e-financial-smoke` job runs a HAND-TYPED list of spec files. It stayed
byte-identical across a delta that added five specs, so order prefill, the
order-ROUTING session window, the mobile orders shell and a whole new regime
tab shipped with no CI browser evidence at all. Nothing compared the list to
the tree, so the list not growing was invisible.

This closes that loop: `web/e2e/*.spec.ts` must be covered by the union of the
ci.yml arg list and `web/e2e/ci-curation-ledger.txt`. A new spec fails here
until someone classifies it.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_E2E = _ROOT / "web" / "e2e"
_LEDGER = _E2E / "ci-curation-ledger.txt"


def _curated() -> set[str]:
    """Spec basenames passed to `npx playwright test` in the workflow."""
    return {
        m.rsplit("/", 1)[-1]
        for m in re.findall(r"^\s+(e2e/[A-Za-z0-9._-]+\.spec\.ts)\s*$", _CI.read_text(), re.M)
    }


def _ledger() -> set[str]:
    return {
        line.strip()
        for line in _LEDGER.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _tree() -> set[str]:
    return {p.name for p in _E2E.glob("*.spec.ts")}


def test_every_spec_is_curated_or_explicitly_held_out() -> None:
    unclassified = sorted(_tree() - _curated() - _ledger())
    assert not unclassified, (
        "These e2e specs are in NEITHER the ci.yml curated list NOR "
        f"web/e2e/ci-curation-ledger.txt: {unclassified}. A spec that is in "
        "neither has zero CI browser evidence and nothing says why. Either "
        "preflight it under a production server "
        '(PLAYWRIGHT_WEBSERVER_CMD="npx next start") and add it to the ci.yml '
        "list, or add it to the ledger with the reason it is held out."
    )


def test_ledger_has_no_stale_entries() -> None:
    """A deleted or newly-curated spec must not linger in the ledger."""
    tree, curated = _tree(), _curated()
    ledger = _ledger()

    gone = sorted(ledger - tree)
    assert not gone, f"ci-curation-ledger.txt names specs that no longer exist: {gone}"

    both = sorted(ledger & curated)
    assert not both, (
        f"These specs are BOTH curated in ci.yml and listed as held out: {both}. "
        "Remove them from the ledger so it keeps telling the truth."
    )


def test_the_five_delta_specs_are_curated() -> None:
    """The five specs T-271 named. Preflighted green 3x under `next start`."""
    expected = {
        "chain-deck-ticket-scroll.spec.ts",
        "leap-order-prefill.spec.ts",
        "mobile-orders-session.spec.ts",
        "orders-session-window.spec.ts",
        "vixts-tab.spec.ts",
    }
    missing = sorted(expected - _curated())
    assert not missing, f"dropped from the curated CI list again: {missing}"
