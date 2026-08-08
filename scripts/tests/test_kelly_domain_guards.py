"""Domain guard tests for kelly.py — malformed bankroll (T-038).

kelly_size_batch and the CLI dollar-sizing path must never emit a NaN or
negative dollar size, regardless of a malformed bankroll (zero, negative,
NaN, or infinite). Pinned values derived independently by hand (and cross-
checked with a read-only interpreter run against the pre-fix source — see
NOTES.md), mirroring test_kelly_vectorized.py's discipline of not deriving
the expected value by calling the function under test.

Two distinct bugs are pinned here:
  1. kelly_size_batch(...) with a negative/NaN/infinite bankroll returns
     negative, NaN, or infinite dollar sizes instead of clamping to 0.
  2. The CLI's `if args.bankroll:` treats an explicit `--bankroll 0` as "not
     supplied" (falsy-zero bug) and silently omits dollar_size/max_per_position
     /use_size from the JSON output instead of reporting them as 0.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from scripts.kelly import kelly_size_batch

# Repo root + kelly.py derived from this file's location — never hardcode an
# absolute local path (it breaks in CI where the checkout is elsewhere).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_KELLY_SCRIPT = Path(__file__).resolve().parent.parent / "kelly.py"


class TestKellySizeBatchBankrollDomainGuards:
    """Strong edge (p=0.9, odds=5.0 -> full_kelly=0.88, well clear of zero) paired
    with a malformed bankroll. Every returned size must be finite and >= 0, and
    exactly 0 for a non-finite or non-positive bankroll — a genuine edge must
    never produce a negative or NaN/inf dollar size just because the bankroll
    input was garbage.
    """

    @pytest.mark.parametrize(
        "bankroll",
        [0.0, -100_000.0, float("nan")],
        ids=["zero", "negative", "nan"],
    )
    def test_malformed_bankroll_yields_zero_size(self, bankroll):
        # Pre-fix: bankroll=0.0 -> [0.] (already correct, arithmetic coincidence).
        #          bankroll=-100_000.0 -> [-22000.] (NEGATIVE — fails >= 0).
        #          bankroll=nan -> [nan] (fails finite).
        result = kelly_size_batch(
            np.array([0.9]), np.array([5.0]), bankroll, fraction=0.25, max_pct=0.025
        )
        assert np.all(np.isfinite(result)), f"non-finite size for bankroll={bankroll}: {result}"
        assert np.all(result >= 0.0), f"negative size for bankroll={bankroll}: {result}"
        assert result[0] == pytest.approx(0.0)

    @pytest.mark.parametrize(
        "bankroll",
        [0.0, -100_000.0, float("nan"), float("inf"), float("-inf")],
        ids=["zero", "negative", "nan", "posinf", "neginf"],
    )
    def test_malformed_bankroll_batch_all_zero(self, bankroll):
        # A 4-candidate batch mixing strong edge, moderate edge, marginal edge,
        # and NO edge (p=0.3/odds=1.0 -> full_kelly<0 -> clipped to 0 pre-cap),
        # to confirm the guard applies uniformly across the whole array rather
        # than only to a single convenient element.
        #
        # Pre-fix bankroll=-100_000.0 is the sharpest illustration of the bug:
        # [-22000., -10000., -2500., -2500.] — even the NO-EDGE candidate
        # (dollar_size=0 pre-cap) comes out at -2500 because
        # np.minimum(0.0, bankroll * max_pct) picks the negative cap.
        # Pre-fix bankroll=inf: [inf, inf, nan, inf] — the no-edge candidate's
        # `inf * 0.0` is NaN under IEEE 754, so the batch isn't even uniformly
        # infinite, it's a mix of inf and nan (with a RuntimeWarning on stderr).
        prob_wins = np.array([0.9, 0.6, 0.3, 0.51])
        odds = np.array([5.0, 2.0, 1.0, 1.02])
        result = kelly_size_batch(prob_wins, odds, bankroll, fraction=0.25, max_pct=0.025)
        assert np.all(np.isfinite(result)), f"non-finite size(s) for bankroll={bankroll}: {result}"
        assert np.all(result >= 0.0), f"negative size(s) for bankroll={bankroll}: {result}"
        np.testing.assert_array_equal(result, np.zeros(4))

    def test_positive_finite_bankroll_unaffected(self):
        # Guardrail on the guard: a normal positive bankroll must size exactly
        # as before (pinned independently, matches
        # test_kelly_vectorized.py::test_hard_cap_exact_value).
        result = kelly_size_batch(
            np.array([0.9]), np.array([5.0]), 50_000.0, fraction=0.25, max_pct=0.025
        )
        assert result[0] == pytest.approx(1_250.0)


class TestKellyCLIBankrollZero:
    """CLI `--bankroll 0` must NOT be treated as "no bankroll supplied".

    `if args.bankroll:` is a falsy-zero bug — `0.0` is falsy in Python, so the
    entire dollar-sizing block (dollar_size/max_per_position/use_size) is
    skipped and those keys are absent from the JSON output, instead of present
    and equal to 0. Mirrors TestKellyCLISizing._run_cli in test_kelly_extended.py.
    """

    def _run_cli(self, prob, odds, fraction, bankroll=None):
        cmd = [
            sys.executable, str(_KELLY_SCRIPT),
            "--prob", str(prob),
            "--odds", str(odds),
            "--fraction", str(fraction),
        ]
        if bankroll is not None:
            cmd += ["--bankroll", str(bankroll)]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
        assert r.returncode == 0, f"CLI failed: {r.stderr}"
        return json.loads(r.stdout)

    def test_bankroll_zero_present_and_zero_sized(self):
        # p=0.9, odds=5.0 -> strong edge (full_kelly_pct=88.0, fractional=22.0),
        # well above zero, so a bug that treats bankroll=0 as "not supplied" is
        # not masked by a coincidentally-zero fractional_kelly_pct.
        # Pre-fix: "dollar_size" / "max_per_position" / "use_size" are ABSENT
        # from the JSON entirely (identical output to omitting --bankroll).
        data = self._run_cli(prob=0.9, odds=5.0, fraction=0.25, bankroll=0)
        assert "dollar_size" in data, "bankroll=0 must still run the sizing block (falsy-zero bug)"
        assert "max_per_position" in data
        assert "use_size" in data
        assert data["dollar_size"] == pytest.approx(0.0)
        assert data["max_per_position"] == pytest.approx(0.0)
        assert data["use_size"] == pytest.approx(0.0)

    def test_bankroll_omitted_has_no_sizing_keys(self):
        # Sanity check on the OTHER branch of the `is not None` fix: omitting
        # --bankroll entirely (argparse default None) must still skip the
        # sizing block exactly as it does today — this is what stops an
        # overcorrection (e.g. deleting the `if` guard entirely, which would
        # crash on `None * float` instead of preserving today's behavior).
        data = self._run_cli(prob=0.9, odds=5.0, fraction=0.25)
        assert "dollar_size" not in data
        assert "max_per_position" not in data
        assert "use_size" not in data
