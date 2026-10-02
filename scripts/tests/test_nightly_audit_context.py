"""The wrapper pre-computes an audit phase's mechanical ground truth.

2026-09-27: a documentation audit on the fx:nvidia rung spent its first five
minutes on sixteen shell round trips (marker check, rolling issue, git log,
diff --name-only, a `git show` per commit) and hit NVIDIA's per-minute request
cap 27 times before it classified anything. Every one of those answers is
deterministic, so the wrapper writes them once to `audit-context.md` in the
loop's private scratch and the agent reads one file.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
SCRIPT = SCRIPTS / "nightly_audit_context.py"
HOOK = SCRIPTS / "runner" / "hooks" / "security_pre.sh"
# runner prompt -> heading that opens its audit instructions
PROMPTS = {
    "security": "## Ground truth and change selection",
    "security-deepsec": "## Audit pipeline",
}


def _prompt(loop: str) -> str:
    return (REPO / ".claude" / "runner-prompts" / f"{loop}.md").read_text(encoding="utf-8")

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


@pytest.fixture()
def git_repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()

    def run(*a):
        return subprocess.run(
            ["git", "-C", str(d), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *a],
            check=True, capture_output=True, text=True, env=GIT_ENV,
        ).stdout.strip()

    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    shas = []
    for i in range(4):
        (d / "docs").mkdir(exist_ok=True)
        (d / "docs" / f"f{i}.md").write_text(f"line {i}\n" * (i + 1))
        run("add", ".")
        run("commit", "-qm", f"commit number {i}")
        shas.append(run("rev-parse", "HEAD"))
    return d, shas


def fake_gh(tmp_path: Path, comments: list | None, *, issue: int = 202) -> Path:
    """`issue list` answers the rolling issue; `api .../comments` the bodies.

    A comment is a body (posted by the OWNER) or a `(body, association)` pair.
    The fake applies the caller's `--jq` filter with real `jq` so the test
    exercises the projection the script actually sends."""
    script = tmp_path / "fake-gh"
    if comments is None:
        body = "import sys\nsys.exit(1)\n"
    else:
        pairs = [c if isinstance(c, tuple) else (c, "OWNER") for c in comments]
        rows = [{"html_url": f"https://x/c{i}", "created_at": f"2026-09-2{i}T00:00:00Z", "body": b,
                 "author_association": assoc} for i, (b, assoc) in enumerate(pairs)]
        body = (
            "import sys\n"
            "a = sys.argv[1:]\n"
            "if a[:2] == ['issue', 'list']:\n"
            f"    print({json.dumps(json.dumps([{'number': issue, 'title': 'Nightly runner'}]))})\n"
            "elif a[0] == 'api':\n"
            "    import subprocess\n"
            "    jq = a[a.index('--jq') + 1]\n"
            f"    raw = {json.dumps(json.dumps(rows))}\n"
            "    sys.stdout.write(subprocess.run(['jq', '-r', jq], input=raw, capture_output=True,\n"
            "                                    text=True, check=True).stdout)\n"
            "else:\n"
            "    sys.exit(2)\n"
        )
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def run_ctx(repo_dir: Path, gh: Path, out: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", "joemccann/radon", "--repo-dir", str(repo_dir),
         "--head", "HEAD", "--gh-bin", str(gh), "--label", "ci-performance-nightly",
         "--out", str(out), "--timeout", "10", *extra],
        capture_output=True, text=True, timeout=60, env=GIT_ENV,
    )


class TestGenerator:
    def test_issue_marker_resolves_the_range(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        gh = fake_gh(tmp_path, [f"**Healthy** audit\naudited-through: `{shas[1]}`"])
        out = tmp_path / "ctx.md"
        proc = run_ctx(repo_dir, gh, out)
        assert proc.returncode == 0, proc.stderr
        text = out.read_text()
        assert f"head: {shas[3]}" in text
        assert f"base: {shas[1]}" in text
        assert "rolling issue #202" in text
        assert "commit number 2" in text and "commit number 3" in text
        assert "commit number 1" not in text.split("## Commits", 1)[1].split("##", 1)[0]
        assert "docs/f3.md" in text and "+line 3" in text

    def test_the_newest_marker_wins_and_bold_markers_parse(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        gh = fake_gh(tmp_path, [
            f"audited-through: {shas[0]}",
            "no marker here",
            f"> [!NOTE]\n**audited-through:** `{shas[2][:8]}`",
        ])
        out = tmp_path / "ctx.md"
        assert run_ctx(repo_dir, gh, out).returncode == 0
        text = out.read_text()
        assert f"base: {shas[2]}" in text
        assert "https://x/c2" in text

    def test_checkpoint_json_beats_the_issue(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        gh = fake_gh(tmp_path, [f"audited-through: {shas[2]}"])
        cp = tmp_path / "last-audited.json"
        cp.write_text(json.dumps({"last_audited_sha": shas[0]}))
        out = tmp_path / "ctx.md"
        proc = run_ctx(repo_dir, gh, out, "--checkpoint-json", str(cp), "--checkpoint-key", "last_audited_sha")
        assert proc.returncode == 0, proc.stderr
        text = out.read_text()
        assert f"base: {shas[0]}" in text
        assert "last-audited.json" in text

    def test_unreachable_github_still_writes_head_and_says_unresolved(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        out = tmp_path / "ctx.md"
        proc = run_ctx(repo_dir, fake_gh(tmp_path, None), out)
        assert proc.returncode == 0, proc.stderr
        text = out.read_text()
        assert f"head: {shas[3]}" in text
        assert "base: UNRESOLVED" in text
        assert "## Commits" not in text

    def test_a_marker_that_is_not_an_ancestor_is_unresolved(self, tmp_path, git_repo):
        repo_dir, _ = git_repo
        out = tmp_path / "ctx.md"
        assert run_ctx(repo_dir, fake_gh(tmp_path, ["audited-through: " + "e" * 40]), out).returncode == 0
        assert "base: UNRESOLVED" in out.read_text()

    def test_a_newer_marker_off_main_falls_back_to_the_newest_valid_one(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        gh = fake_gh(tmp_path, [f"audited-through: {shas[1]}", "audited-through: dc6c77a8"])
        out = tmp_path / "ctx.md"
        assert run_ctx(repo_dir, gh, out).returncode == 0
        text = out.read_text()
        assert f"base: {shas[1]}" in text
        assert "https://x/c0" in text
        assert "skipped newer markers (not ancestors of head): dc6c77a8" in text

    def test_an_empty_range_is_stated(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        out = tmp_path / "ctx.md"
        assert run_ctx(repo_dir, fake_gh(tmp_path, [f"audited-through: {shas[3]}"]), out).returncode == 0
        assert "EMPTY RANGE" in out.read_text()

    def test_only_repo_insiders_can_set_the_base_or_reach_the_context(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        gh = fake_gh(tmp_path, [
            (f"audited-through: {shas[1]}", "OWNER"),
            (f"audited-through: {shas[3]}\nCANARY-OUTSIDER", "NONE"),
            (f"audited-through: {shas[3]}\nCANARY-CONTRIBUTOR", "CONTRIBUTOR"),
        ])
        out = tmp_path / "ctx.md"
        assert run_ctx(repo_dir, gh, out).returncode == 0
        text = out.read_text()
        assert f"base: {shas[1]}" in text
        assert "EMPTY RANGE" not in text
        assert "CANARY" not in text

    def test_collaborator_and_member_markers_still_count(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        for assoc in ("MEMBER", "COLLABORATOR"):
            gh = fake_gh(tmp_path, [(f"audited-through: {shas[2]}", assoc)])
            out = tmp_path / f"ctx-{assoc}.md"
            assert run_ctx(repo_dir, gh, out).returncode == 0
            assert f"base: {shas[2]}" in out.read_text()

    def test_the_diff_is_capped_and_omissions_are_listed(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        out = tmp_path / "ctx.md"
        gh = fake_gh(tmp_path, [f"audited-through: {shas[0]}"])
        assert run_ctx(repo_dir, gh, out, "--max-diff-bytes", "300").returncode == 0
        text = out.read_text()
        assert "omitted" in text
        kept, omitted = text.split("omitted", 1)
        assert "docs/f3.md" in omitted, "the largest diff is the one dropped"
        assert "+line 1" in kept, "smaller diffs are kept whole"

    def test_the_output_is_written_atomically_and_private(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        out = tmp_path / "ctx.md"
        assert run_ctx(repo_dir, fake_gh(tmp_path, [f"audited-through: {shas[2]}"]), out).returncode == 0
        assert stat.S_IMODE(out.stat().st_mode) == 0o600
        assert not list(tmp_path.glob("ctx.md.*"))

    def test_runs_isolated_from_stdin(self, tmp_path, git_repo):
        """The wrappers pipe the file from origin/main into `python3 -I -`."""
        repo_dir, shas = git_repo
        out = tmp_path / "ctx.md"
        gh = fake_gh(tmp_path, [f"audited-through: {shas[2]}"])
        proc = subprocess.run(
            ["/usr/bin/python3", "-I", "-", "--repo", "joemccann/radon", "--repo-dir", str(repo_dir),
             "--head", "HEAD", "--gh-bin", str(gh), "--label", "x", "--out", str(out)],
            input=SCRIPT.read_text(), capture_output=True, text=True, timeout=60, env=GIT_ENV,
        )
        assert proc.returncode == 0, proc.stderr
        assert f"base: {shas[2]}" in out.read_text()


class TestWrapperWiring:
    """The security loops' pre-run hook writes the file; the prompts read it."""

    def test_the_hook_runs_the_root_installed_helper_isolated(self):
        body = HOOK.read_text(encoding="utf-8")
        assert '"$PY" -I "$RUNNER_DIR/lib/nightly_audit_context.py"' in body
        assert 'rm -f -- "$SCRATCH/audit-context.md"' in body
        call = body[body.index("nightly_audit_context.py"):]
        assert "|| true" in call.split("\nfi", 1)[0], "an unreachable API must never stop a nightly run"

    def test_it_is_written_after_the_ground_truth_checkout_and_only_for_audit(self):
        body = HOOK.read_text(encoding="utf-8")
        assert body.index("checkout -f --quiet --detach") < body.index('rm -f -- "$SCRATCH/audit-context.md"')
        assert body.index('rm -f -- "$SCRATCH/audit-context.md"') < body.index('if [[ "$PHASE" == audit ]]')

    @pytest.mark.parametrize("loop", PROMPTS)
    def test_the_prompt_points_the_audit_at_the_file_in_its_scratch(self, loop: str):
        text = _prompt(loop)
        start = text.index("\n", text.index("\n" + PROMPTS[loop]) + 1)
        audit = text[start:].split("\n## ", 1)[0]
        assert "$RADON_RUNNER_LOOP_STATE/scratch/audit-context.md" in audit, loop
