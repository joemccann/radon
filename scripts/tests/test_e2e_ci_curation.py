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

import os
import re
import subprocess
from pathlib import Path

import pytest

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

# ── T-447: site/e2e has NO CI Playwright job at all ──────────────────────────
#
# The web guard above compares web/e2e to the ci.yml curated list. site/e2e has
# no CI job that runs it, so every site spec is a hold-out by construction —
# but until site/e2e/ci-curation-ledger.txt existed, nothing recorded that, and
# agent-prompt-recipes.spec.ts / libraries-fx-pack.spec.ts landed reachable by
# zero CI jobs invisibly. This guard reds when a site spec is missing from the
# site ledger (or lingers after deletion), so future additions cannot be
# silently invisible.

_SITE_E2E = _ROOT / "site" / "e2e"
_SITE_LEDGER = _SITE_E2E / "ci-curation-ledger.txt"


def _site_ledger() -> set[str]:
    return {
        line.strip()
        for line in _SITE_LEDGER.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _site_tree() -> set[str]:
    return {p.name for p in _SITE_E2E.glob("*.spec.ts")}


def test_every_site_spec_is_in_the_site_ledger() -> None:
    missing = sorted(_site_tree() - _site_ledger())
    assert not missing, (
        f"These site/e2e specs are not in site/e2e/ci-curation-ledger.txt: {missing}. "
        "NO CI job runs site Playwright, so a spec absent from the ledger has "
        "zero CI browser evidence and nothing says why. Add it to the ledger "
        "with a dated REVIEWED entry, or add a verified-green site Playwright "
        "CI job and curate it there."
    )


def test_site_ledger_has_no_stale_entries() -> None:
    gone = sorted(_site_ledger() - _site_tree())
    assert not gone, f"site/e2e/ci-curation-ledger.txt names specs that no longer exist: {gone}"


# ── T-438: a CHANGED held-out spec needs evidence, not just a classification ──
#
# The guard above asks whether every spec is CLASSIFIED. It never asked whether
# a spec that changed was re-run anywhere. chat-launcher-focus.spec.ts and
# open-order-combo.spec.ts were both edited in the 0202e32d..2b936ebc delta and
# are both held out (ci-curation-ledger.txt), so their changes shipped with zero
# browser evidence in CI and the ledger, being a frozen list of bare names, said
# nothing about it. A hold-out is a standing decision; a change to the spec is a
# new event, and the ledger has to be re-stamped for it.

_ANNOTATION = re.compile(
    r"^#\s*REVIEWED\s+(\d{4}-\d{2}-\d{2})\s+([A-Za-z0-9._-]+\.spec\.ts)\b"
)


def _git(*args: str) -> str | None:
    """Local git only. Returns None on any failure -- never raises, never fetches."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_ROOT), *args],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _merge_base() -> str | None:
    """The commit this branch forked from, resolved WITHOUT network.

    Only refs already in the local object store are consulted, so a shallow or
    detached checkout that has none of them degrades to a skip rather than a
    false red. ``RADON_E2E_CURATION_BASE`` pins the base explicitly (CI can set
    it from the PR base; the mutation drill for this guard uses it too).
    """
    pinned = os.environ.get("RADON_E2E_CURATION_BASE")
    if pinned:
        return _git("merge-base", "HEAD", pinned) or _git(
            "rev-parse", "--verify", pinned + "^{commit}"
        )
    candidates = []
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        candidates += ["origin/" + base_ref, base_ref]
    candidates += ["origin/main", "main"]
    for ref in candidates:
        if _git("rev-parse", "--verify", ref + "^{commit}") is None:
            continue
        base = _git("merge-base", "HEAD", ref)
        if base:
            return base
    return None


def _annotations() -> dict[str, str]:
    """spec basename -> the LATEST dated REVIEWED stamp for it."""
    latest: dict[str, str] = {}
    for line in _LEDGER.read_text().splitlines():
        m = _ANNOTATION.match(line.strip())
        if m and m.group(1) > latest.get(m.group(2), ""):
            latest[m.group(2)] = m.group(1)
    return latest


def test_a_changed_heldout_spec_carries_a_dated_ledger_annotation() -> None:
    base = _merge_base()
    if base is None:
        pytest.skip(
            "no local merge-base: neither RADON_E2E_CURATION_BASE, "
            "$GITHUB_BASE_REF, origin/main nor main resolves in this checkout "
            "(shallow clone, or a tree with no upstream ref). This guard reads "
            "only the local object store and never fetches, so it declines "
            "rather than guessing a base."
        )
    changed = _git("diff", "--name-only", base, "HEAD", "--", "web/e2e") or ""
    specs = {
        name.rsplit("/", 1)[-1]
        for name in changed.splitlines()
        if name.endswith(".spec.ts") and (_ROOT / name).is_file()
    }
    # A clean checkout sitting ON the base has an empty diff and passes here;
    # only COMMITTED changes count, so uncommitted local edits never red it.
    heldout_changed = sorted(specs & _ledger())
    if not heldout_changed:
        return

    annotations = _annotations()
    problems = []
    for spec in heldout_changed:
        # T-469: %as (author date) is stable across rebase/amend/cherry-pick;
        # %cs is rewritten to "now" and re-reds a correctly-stamped spec.
        changed_on = _git(
            "log", "-1", "--format=%as", base + "..HEAD", "--", "web/e2e/" + spec
        )
        stamped = annotations.get(spec)
        if stamped is None:
            problems.append(spec + ": changed on " + str(changed_on) + ", no REVIEWED stamp")
        elif changed_on and stamped < changed_on:
            problems.append(
                spec + ": changed on " + changed_on + ", stamp is stale (" + stamped + ")"
            )

    assert not problems, (
        "These e2e specs were MODIFIED in this branch and are held out of the "
        f"CI Playwright run, so the change has no browser evidence: {problems}. "
        "Either curate the spec in the e2e-financial-smoke job, or add a "
        "`# REVIEWED <YYYY-MM-DD> <spec> - <how it was verified>` line to "
        "web/e2e/ci-curation-ledger.txt dated on or after the change."
    )
