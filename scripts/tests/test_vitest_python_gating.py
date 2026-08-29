"""The vitest shards must not --exclude whole test FILES (T-276).

Three mixed files -- ``web/tests/integration.test.ts``,
``lib/tools/__tests__/kelly.test.ts`` and
``lib/tools/__tests__/runner.test.ts`` -- were dropped from every vitest shard
by ``--exclude`` flags in ``ci.yml`` because a MINORITY of their tests spawn a
real ``python3.13`` that the Bun-only job does not have. Effect: 27 tests ran
nowhere (excluded in CI, and 9 of them red on any developer machine without
python3.13 on PATH), including the 2026-05-22 bare-``python3.13`` outage
regression, which needs no subprocess at all.

The subprocess tests are now gated per TEST by ``hasPython313()``. This file
holds that line: it fails if a file-level ``--exclude`` returns, and it fails
if a subprocess test loses its guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"

# Files that used to be excluded wholesale, with the count of tests in each
# that spawn a real interpreter and so must carry a python313 guard.
_FORMERLY_EXCLUDED = {
    "web/tests/integration.test.ts": 2,
    "lib/tools/__tests__/kelly.test.ts": 1,  # one guarded describe, four tests
    "lib/tools/__tests__/runner.test.ts": 4,
}

_GUARD = "!hasPython313()"


@pytest.mark.parametrize("relpath", sorted(_FORMERLY_EXCLUDED))
def test_ci_does_not_exclude_the_file(relpath: str) -> None:
    """No ``--exclude`` may name these paths anywhere in the workflow."""
    text = _CI.read_text()
    basename = relpath.rsplit("/", 1)[-1]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "--exclude" in stripped and basename in stripped:
            pytest.fail(
                f"{relpath} is --exclude'd again in ci.yml ({stripped!r}). "
                "A file-level exclude also drops the python-FREE tests in that "
                "file, which is exactly T-276. Gate the subprocess tests with "
                "hasPython313() instead."
            )


@pytest.mark.parametrize("relpath,expected_guards", sorted(_FORMERLY_EXCLUDED.items()))
def test_subprocess_tests_are_guarded_by_name(relpath: str, expected_guards: int) -> None:
    """Each subprocess block keeps its skipIf guard AND its reason label."""
    text = (_ROOT / relpath).read_text()

    guards = text.count(_GUARD)
    assert guards == expected_guards, (
        f"{relpath}: expected {expected_guards} `{_GUARD}` guard(s), found {guards}. "
        "A subprocess test without one goes RED on the Bun-only CI shard; an "
        "extra one silently stops running a python-free test."
    )

    # Every guard must pair with python313Label(), or the skip is invisible in
    # the reporter output and we are back to a silent exclusion.
    labels = len(re.findall(r"python313Label\(", text))
    assert labels == expected_guards, (
        f"{relpath}: {guards} skipIf guard(s) but {labels} python313Label(...) "
        "call(s). A skip with no stated reason is the failure mode T-276 names."
    )


def test_helper_probes_a_real_interpreter() -> None:
    """The probe must actually spawn python3.13, not read an env var."""
    helper = (_ROOT / "web" / "tests" / "helpers" / "python313.ts").read_text()
    assert 'spawnSync("python3.13"' in helper
    assert "import numpy" in helper, (
        "scripts/kelly.py imports numpy, so a bare interpreter is not enough "
        "to run the gated tests."
    )
