"""Pre-compute a nightly audit phase's mechanical ground truth in one file.

2026-09-27: a documentation audit on the fx:nvidia rung spent its first five
minutes on sixteen shell round trips (rolling issue, `git log`, `git diff
--name-only`, one `git show` per commit) and hit NVIDIA's per-minute request
cap 27 times before it classified a single change. Every one of those answers
is deterministic. The wrapper now writes them once, after `ground_truth`, to
`audit-context.md` in the loop's private scratch; the agent reads one file and
spends its requests on judgement.

The base is the newest `audited-through:` marker on the loop's rolling issue,
or, for the security loops, a key in their private `last-audited.json`. A base
that cannot be resolved, or is not an ancestor of HEAD, is reported as
UNRESOLVED and the agent falls back to its own procedure. An unreachable API
never fails the run: the file still carries HEAD.

Stdlib only, 3.9-clean (invoked via ``python3 -I -``, same as
``nightly_green_base.py``). ``gh`` and ``git`` do all the I/O.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

MARKER = re.compile(r"audited-through:?\**\s*`?([0-9a-f]{7,40})\b", re.IGNORECASE)
#: Generated or vendored paths whose diff is noise to every audit.
DIFF_EXCLUDES = (
    ":(exclude)tools/codemap/*.json",
    ":(exclude)tools/codemap/codemap.data.js",
    ":(exclude)**/package-lock.json",
    ":(exclude)**/*.lock",
    ":(exclude).codex/skills/**",
    ":(exclude).claude/portable-prompts/**",
)
ISSUE_BODY_CAP = 12000
#: Only comments from repository insiders may set the base or reach the agent's
#: context; the rolling issues are public and anyone can comment on them.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def git(repo_dir: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, *args], capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def gh(gh_bin: str, args: list, timeout: int) -> str | None:
    try:
        proc = subprocess.run([gh_bin, *args], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def rolling_issue(gh_bin: str, repo: str, label: str, timeout: int):
    """(number, title, marker comments oldest first), or None if unreachable."""
    out = gh(gh_bin, ["issue", "list", "--repo", repo, "--label", label, "--state", "open",
                      "--limit", "1", "--json", "number,title"], timeout)
    try:
        rows = json.loads(out) if out else None
    except json.JSONDecodeError:
        rows = None
    if not rows:
        return None
    number, title = rows[0]["number"], rows[0].get("title", "")
    out = gh(gh_bin, ["api", f"repos/{repo}/issues/{number}/comments?per_page=100", "--paginate",
                      "--jq", ".[] | {html_url, created_at, body, author_association} | @json"], timeout)
    marked = []
    for line in (out or "").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("author_association") not in TRUSTED_ASSOCIATIONS:
            continue
        if MARKER.search(row.get("body") or ""):
            marked.append(row)
    return number, title, marked


def checkpoint_from_json(path: str, key: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh).get(key, "")
    except (OSError, ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


def verify_base(repo_dir: str, sha: str, head: str) -> str:
    full = (git(repo_dir, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}") or "").strip()
    if not full:
        return ""
    proc = subprocess.run(["git", "-C", repo_dir, "merge-base", "--is-ancestor", full, head],
                          capture_output=True, check=False)
    return full if proc.returncode == 0 else ""


def capped_diff(repo_dir: str, rng: str, cap: int) -> str:
    files = (git(repo_dir, "diff", "--name-only", rng, "--", ".", *DIFF_EXCLUDES) or "").split()
    chunks = {path: git(repo_dir, "diff", rng, "--", path) or "" for path in files}
    parts, used, omitted = [], 0, []
    # Smallest first: the cap keeps as many whole files as fit, and the big
    # ones it drops are exactly the ones worth a targeted read.
    for path in sorted(files, key=lambda f: len(chunks[f])):
        chunk = chunks[path]
        if used + len(chunk) > cap:
            omitted.append(path)
            continue
        parts.append(chunk)
        used += len(chunk)
    text = "".join(parts)
    if omitted:
        text += (f"\n# {len(omitted)} file(s) omitted over the {cap}-byte cap; "
                 "read them with `git diff <range> -- <path>`:\n")
        text += "".join(f"#   {p}\n" for p in omitted)
    return text


def fence(body: str) -> str:
    return "```text\n" + body.rstrip("\n") + "\n```\n"


def render(args) -> str:
    head = (git(args.repo_dir, "rev-parse", args.head) or "").strip()
    lines = [
        "# Audit context (pre-computed by the nightly wrapper)",
        "",
        f"generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"head: {head or 'UNRESOLVED'}",
    ]
    base, source, issue = "", "", None
    comment = None
    if args.checkpoint_json:
        raw = checkpoint_from_json(args.checkpoint_json, args.checkpoint_key)
        if raw:
            base, source = raw, f"{os.path.basename(args.checkpoint_json)} key {args.checkpoint_key}"
    if args.label:
        issue = rolling_issue(args.gh_bin, args.repo, args.label, args.timeout)
    verified, comment, skipped = "", None, []
    if base and head:
        verified = verify_base(args.repo_dir, base, head)
    elif issue and head:
        # Newest marker that is a real ancestor of HEAD. A marker naming a
        # commit that never reached main (dc6c77a8 on reliability, 2026-09-27)
        # is skipped, not trusted.
        for row in reversed(issue[2]):
            sha = MARKER.search(row["body"]).group(1)
            verified = verify_base(args.repo_dir, sha, head)
            if verified:
                comment = row
                source = f"rolling issue #{issue[0]} comment {row.get('html_url', '')}"
                break
            skipped.append(sha)
        base = skipped[0] if skipped and not verified else base
    if verified:
        lines.append(f"base: {verified}")
        lines.append(f"base source: {source}")
        if skipped:
            lines.append(f"skipped newer markers (not ancestors of head): {', '.join(skipped)}")
    else:
        why = f"marker {base} is not an ancestor of head" if base else "no audited-through marker found"
        lines.append(f"base: UNRESOLVED ({why}); resolve it with your own procedure")
    lines.append("")
    if issue:
        lines += [f"## Rolling issue", "", f"rolling issue #{issue[0]}: {issue[1]}", ""]
        comment = comment or (issue[2][-1] if issue[2] else None)
        if comment:
            body = comment["body"]
            if len(body) > ISSUE_BODY_CAP:
                body = body[:ISSUE_BODY_CAP] + "\n[... truncated]"
            lines += [f"checkpoint comment ({comment.get('created_at', '')}, "
                      f"{comment.get('html_url', '')}):", "", fence(body)]
    elif args.label:
        lines += ["## Rolling issue", "", f"UNAVAILABLE: could not read the `{args.label}` issue", ""]
    if not verified:
        return "\n".join(lines)
    rng = f"{verified}..{head}"
    if verified == head:
        lines += [f"## Range {rng}", "", "EMPTY RANGE: nothing merged since the last audit.", ""]
        return "\n".join(lines)
    lines += [
        f"## Commits ({rng})", "", fence(git(args.repo_dir, "log", "--oneline", rng) or ""),
        "## Changed files", "", fence(git(args.repo_dir, "diff", "--stat=200", rng) or ""),
        "## Per-commit stat", "", fence(git(args.repo_dir, "log", "--stat=200", "--format=%n%h %s", rng) or ""),
        f"## Diff (generated paths excluded, capped at {args.max_diff_bytes} bytes)", "",
        "```diff", capped_diff(args.repo_dir, rng, args.max_diff_bytes).rstrip("\n"), "```", "",
    ]
    return "\n".join(lines)


def write_private(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--repo", required=True)
    p.add_argument("--repo-dir", required=True)
    p.add_argument("--head", default="HEAD")
    p.add_argument("--gh-bin", default="gh")
    p.add_argument("--label", default="")
    p.add_argument("--checkpoint-json", default="")
    p.add_argument("--checkpoint-key", default="")
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--max-diff-bytes", type=int, default=100000)
    args = p.parse_args(argv)
    write_private(args.out, render(args))
    print(f"audit context written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
