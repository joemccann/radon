"""T-490: the runner clone must materialise every tracked path, `.codex/` included.

2026-09-08: `~/radon-weekend/radon-testing` was hand-configured with a sparse
checkout (`/*` + `!/.codex/`) as a workaround for a sandbox that refused
`.codex` writes. The eight tracked `.codex/skills/**` files therefore never
hit disk, `git status` stayed clean, and `test_portable_prompt_sync.py`
reported 21 failures on every nightly audit from 2026-09-17 on. `ground_truth`
resets to origin/main each cycle, so it is the place that must undo a stray
sparse configuration before any test reads the tree.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPERS = [
    REPO / "scripts" / n
    for n in (
        "ci_performance_nightly.sh",
        "documentation_nightly.sh",
        "reliability_weekend.sh",
        "security_deepsec_nightly.sh",
        "security_nightly.sh",
        "testing_weekend.sh",
    )
]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout


def _extract_ground_truth(wrapper: Path) -> str:
    text = wrapper.read_text(encoding="utf-8")
    start = text.index("ground_truth() {")
    end = text.index("\n}", start) + 2
    return text[start:end]


def _sparse_clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / ".codex" / "skills").mkdir(parents=True)
    (seed / ".codex" / "skills" / "SKILL.md").write_text("manual\n")
    (seed / "README").write_text("x\n")
    _git(seed, "add", ".codex", "README")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "core.sparseCheckout", "true")
    (clone / ".git" / "info" / "sparse-checkout").write_text("/*\n!/.codex/\n")
    _git(clone, "read-tree", "-mu", "HEAD")
    assert not (clone / ".codex").exists(), "fixture must start without .codex"
    return clone


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
def test_ground_truth_undoes_a_sparse_checkout_that_hides_codex(tmp_path, wrapper):
    clone = _sparse_clone(tmp_path)
    driver = "\n".join(
        [
            "set -eo pipefail",
            "fetch_origin_with_retry() { git fetch origin --quiet; }",
            "resolve_green_main_sha() { :; }",
            "align_agent_gitdir() { :; }",
            _extract_ground_truth(wrapper),
            "ground_truth",
        ]
    )
    proc = subprocess.run(
        ["bash", "-c", driver], cwd=clone, env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "REPO": str(clone), "HOST_GITDIR": str(clone / ".git"), "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert (clone / ".codex" / "skills" / "SKILL.md").read_text() == "manual\n"
    assert _git(clone, "config", "--get", "core.sparseCheckout").strip() != "true"
