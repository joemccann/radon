"""Ensure an open GitHub PR exists for a VPS/grok incident-response fix.

After IR lands a commit on ``fix/**`` and pushes it, call this helper.
It creates a PR against ``main`` when none is open for that head, and
no-ops when one already exists. It never merges.

Fail closed: ``GROK_PAGE_AUTOPUSH`` not truthy, a private identifier in
the title, body or branch (``ir_push_gate``), a body without the six real IR
sections (``ir_pr_description``), missing ``gh``, unauthenticated ``gh``, or
a token without ``pull_requests: write``. Branch-only is not a ship.

Fine-grained PAT on ``joemccann/radon``: Contents Read/Write and Pull
requests Read/Write. Do not grant Administration or merge bypass.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path
from typing import Callable

import github_pr_output as pr_fmt
import ir_pr_description
import ir_push_gate
from credential_redaction import scrub_credential_text

IR_BRANCH_PREFIX = "fix/"
DEFAULT_BASE = "main"
DEFAULT_REPO = "joemccann/radon"
DEFAULT_NEXT = (
    "Joe reviews and merges after CI green. This automation never merges."
)
GH_MISSING_ERROR = (
    "gh CLI is missing. Install gh and authenticate with a PAT that has "
    "Contents R/W and Pull requests R/W. Branch-only is not a ship."
)
GH_UNAUTH_ERROR = (
    "gh CLI is not authenticated. Authenticate with a PAT that has "
    "Contents R/W and Pull requests R/W. Branch-only is not a ship."
)
PAT_SCOPE_ERROR = (
    "GitHub token cannot open a pull request. Fine-grained PAT needs "
    "Contents: Read and write and Pull requests: Read and write "
    "(pull_requests). Do not grant Administration or merge bypass. "
    "Branch-only is not a ship."
)
_UNSAFE_REF = re.compile(r"[\s\\]")
_EXISTING_PR_URL = re.compile(r"https://github\.com/[^\s]+/pull/\d+")
_PAT_MARKERS = (
    "resource not accessible by personal access token",
    "http 403",
    "403 forbidden",
)

SUMMARY_MAX_CHARS = 300
_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f]+")

Runner = Callable[..., object]


def sanitize_summary(text: str) -> str:
    """Flatten page-derived grok output to one control-free line.

    The summary comes from untrusted incident-page text; it may not carry
    newlines or control characters into a PR title or body.
    """
    return _CONTROL_RUN.sub(" ", text or "").strip()[:SUMMARY_MAX_CHARS]


class IrEnsurePrError(Exception):
    """Operator-visible failure. ``code`` is the process exit status."""

    def __init__(self, message: str, *, code: int = 2):
        super().__init__(message)
        self.code = code


def is_ir_branch(name: str) -> bool:
    """True for ``fix/<slug>`` (and ``origin/fix/<slug>``). Nothing else."""
    if not isinstance(name, str) or not name:
        return False
    ref = name.removeprefix("origin/")
    if _UNSAFE_REF.search(ref) or ".." in ref:
        return False
    return ref.startswith(IR_BRANCH_PREFIX) and len(ref) > len(IR_BRANCH_PREFIX)


def format_ir_pr_title(
    *,
    issue: str,
    incident_id: str | None = None,
) -> str:
    summary = pr_fmt._title_summary(issue, field="issue")
    prefix = f"IR {incident_id}: " if incident_id else "IR: "
    title = prefix + summary
    if len(title) > pr_fmt.GITHUB_PR_TITLE_MAX:
        return title[: pr_fmt.GITHUB_PR_TITLE_MAX]
    return title


def format_ir_pr_body(
    *,
    issue: str,
    fix: str,
    next_action: str | None = None,
    incident_id: str | None = None,
    case_id: str | None = None,
) -> str:
    extras = []
    if incident_id:
        extras.append(f"Incident `{incident_id}`.")
    if case_id:
        extras.append(f"Runbook `docs/incident-runbook.md#{case_id}`.")
    issue_text = issue
    if extras:
        issue_text = f"{issue}\n" + " ".join(extras)
    return pr_fmt.format_pr_body(
        issue=issue_text,
        fix=fix,
        next_action=next_action or DEFAULT_NEXT,
    )


def _default_runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    # Token scan, not positional: flag-shuffled subcommand spellings and
    # `gh api .../pulls/N/merge` are merges too.
    if any(tok == "merge" or tok.endswith("/merge") for tok in argv[1:]):
        raise IrEnsurePrError("IR automation never merges")
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=kwargs.get("timeout", 60),
        cwd=kwargs.get("cwd"),
    )


def _resolve_gh(
    gh_bin: str | None,
    which: Callable[[str], str | None] | None,
) -> str:
    if gh_bin:
        return gh_bin
    finder = which or shutil.which
    found = finder("gh")
    if not found:
        raise IrEnsurePrError(GH_MISSING_ERROR)
    return found


def _is_pat_scope_error(blob: str) -> bool:
    lower = blob.lower()
    return any(marker in lower for marker in _PAT_MARKERS)


def _raise_from_gh(proc: object) -> None:
    blob = f"{getattr(proc, 'stderr', '')}\n{getattr(proc, 'stdout', '')}"
    if _is_pat_scope_error(blob):
        raise IrEnsurePrError(PAT_SCOPE_ERROR)
    detail = blob.strip() or "unknown error"
    raise IrEnsurePrError(
        f"gh failed: {detail}. Branch-only is not a ship."
    )


def _require_auth(runner: Runner, binary: str) -> None:
    proc = runner([binary, "auth", "status"])
    if getattr(proc, "returncode", 1) != 0:
        raise IrEnsurePrError(GH_UNAUTH_ERROR)


def _existing_pr_url(blob: str) -> str | None:
    match = _EXISTING_PR_URL.search(blob)
    return match.group(0) if match else None


def _number_from_url(url: str) -> int | None:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _list_open_pr(
    runner: Runner,
    binary: str,
    *,
    head: str,
    base: str,
    repo: str,
    include_terminal: bool = False,
) -> dict | None:
    proc = runner([
        binary, "pr", "list",
        "--repo", repo,
        "--head", head,
        "--base", base,
        "--state", "all" if include_terminal else "open",
        "--json", "number,url,title,state" if include_terminal else "number,url,title",
    ])
    if getattr(proc, "returncode", 1) != 0:
        _raise_from_gh(proc)
    try:
        rows = json.loads(getattr(proc, "stdout", "") or "[]")
    except ValueError:
        raise IrEnsurePrError(
            "gh pr list returned non-JSON. Branch-only is not a ship."
        ) from None
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    if not isinstance(row, dict) or not row.get("url"):
        return None
    return row


def _create_pr(
    runner: Runner,
    binary: str,
    *,
    head: str,
    base: str,
    repo: str,
    title: str,
    body: str,
) -> dict:
    proc = runner([
        binary, "pr", "create",
        "--repo", repo,
        "--base", base,
        "--head", head,
        "--title", title,
        "--body", body,
    ])
    blob = f"{getattr(proc, 'stderr', '')}\n{getattr(proc, 'stdout', '')}"
    if getattr(proc, "returncode", 1) != 0:
        url = _existing_pr_url(blob)
        if url:
            return {
                "action": "exists",
                "url": url,
                "number": _number_from_url(url),
                "head": head,
            }
        _raise_from_gh(proc)
    lines = [ln.strip() for ln in (getattr(proc, "stdout", "") or "").splitlines() if ln.strip()]
    url = lines[-1] if lines else ""
    if not url.startswith("http"):
        url = _existing_pr_url(blob) or ""
    if not url:
        raise IrEnsurePrError(
            "gh pr create succeeded without a URL. Branch-only is not a ship."
        )
    return {
        "action": "created",
        "url": url,
        "number": _number_from_url(url),
        "head": head,
    }


def _update_pr_body(
    runner: Runner,
    binary: str,
    *,
    repo: str,
    number: int,
    body: str,
) -> None:
    # REL-294 / R-713: gh pr edit queries retired Projects-classic fields.
    # REST accepts the exact body without that unrelated GraphQL dependency.
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", encoding="utf-8") as payload:
        json.dump({"body": body}, payload)
        payload.flush()
        proc = runner([
            binary, "api", "--method", "PATCH", f"repos/{repo}/pulls/{number}",
            "--input", payload.name,
        ])
    if getattr(proc, "returncode", 1) != 0:
        _raise_from_gh(proc)


def _require_ir_description(body: str, *, head: str) -> None:
    """A PR body must carry the six real IR sections, never a placeholder."""
    try:
        ir_pr_description.validate_ir_description(body, branch=head)
    except ir_pr_description.IrDescriptionError as exc:
        raise IrEnsurePrError(
            f"IR PR description invalid: {exc}. Build it from the fix "
            "commit (ir_pr_description.description_from_commit)."
        ) from exc


def ensure_pr(
    *,
    head: str,
    issue: str,
    fix: str,
    next_action: str | None = None,
    incident_id: str | None = None,
    case_id: str | None = None,
    base: str = DEFAULT_BASE,
    repo: str = DEFAULT_REPO,
    runner: Runner | None = None,
    gh_bin: str | None = None,
    which: Callable[[str], str | None] | None = None,
    include_terminal: bool = False,
    title: str | None = None,
    body: str | None = None,
    update_existing: bool = False,
    repo_root: Path | None = None,
    scan_base: str | None = None,
) -> dict:
    """Create the IR PR if missing. Never merge. Fail closed on gh/PAT.

    With ``repo_root`` and ``scan_base`` the branch's commits and diff are
    scanned for private identifiers too.
    """
    if not is_ir_branch(head):
        raise IrEnsurePrError(
            f"head must match {IR_BRANCH_PREFIX}* (got {head!r})"
        )
    head = head.removeprefix("origin/")
    raw_title = title or format_ir_pr_title(issue=issue, incident_id=incident_id)
    raw_body = body or format_ir_pr_body(
        issue=issue,
        fix=fix,
        next_action=next_action,
        incident_id=incident_id,
        case_id=case_id,
    )
    # Refuse, never silently redact: title, body and branch are public.
    try:
        ir_push_gate.check_publish(
            repo=repo_root,
            base=scan_base,
            ref=head,
            title=raw_title,
            body=raw_body,
            runner=runner,
        )
    except ir_push_gate.IrPushRefused as exc:
        raise IrEnsurePrError(str(exc)) from exc
    run = runner or _default_runner
    binary = _resolve_gh(gh_bin, which)
    _require_auth(run, binary)
    # Safety net for generic `password=` shapes the refusal scan leaves to it.
    resolved_title = scrub_credential_text(raw_title)
    resolved_body = scrub_credential_text(raw_body)
    existing = _list_open_pr(
        run, binary, head=head, base=base, repo=repo, include_terminal=include_terminal
    )
    if existing:
        if update_existing and body:
            _require_ir_description(resolved_body, head=head)
            number = _number_from_url(existing["url"])
            if number is None:
                raise IrEnsurePrError("existing PR has no valid number")
            _update_pr_body(run, binary, repo=repo, number=number, body=resolved_body)
        return {
            "action": existing["state"].lower() if include_terminal and existing.get("state") in {"CLOSED", "MERGED"} else "exists",
            "url": existing["url"],
            "number": existing.get("number"),
            "head": head,
        }
    _require_ir_description(resolved_body, head=head)
    return _create_pr(
        run,
        binary,
        head=head,
        base=base,
        repo=repo,
        title=resolved_title,
        body=resolved_body,
    )


def infer_ir_head(
    repo_root: Path,
    *,
    runner: Runner | None = None,
) -> str | None:
    """Current ``fix/**`` branch, else the newest local ``fix/**`` ref."""
    run = runner or _default_runner
    current = _git_stdout(
        run, ["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root
    )
    if is_ir_branch(current):
        return current.removeprefix("origin/")
    listed = _git_stdout(
        run,
        [
            "git", "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)",
            "refs/heads/fix/*",
        ],
        repo_root,
    )
    for line in listed.splitlines():
        if is_ir_branch(line):
            return line.removeprefix("origin/")
    return None


def _git_stdout(runner: Runner, argv: list[str], cwd: Path) -> str:
    proc = runner(argv, cwd=cwd)
    if getattr(proc, "returncode", 1) != 0:
        return ""
    return (getattr(proc, "stdout", "") or "").strip()


def ensure_after_code_fix(
    repo_root: Path,
    *,
    page: dict,
    summary: str,
    runner: Runner | None = None,
    gh_bin: str | None = None,
    which: Callable[[str], str | None] | None = None,
    head: str | None = None,
) -> dict:
    """Poller backstop: open a PR for the branch grok just pushed."""
    resolved = head or infer_ir_head(repo_root, runner=runner)
    if not resolved:
        raise IrEnsurePrError(
            "no fix/** branch found after code_fix; grok must push "
            f"{IR_BRANCH_PREFIX}<slug>. Branch-only is not a ship."
        )
    run = runner or _default_runner
    commit = _git_stdout(run, ["git", "log", "-1", "--format=%B"], repo_root)
    try:
        title_summary, body = ir_pr_description.description_from_commit(
            commit, branch=resolved, page=page
        )
    except ir_pr_description.IrDescriptionError as exc:
        raise IrEnsurePrError(f"IR PR description invalid: {exc}") from exc
    issue = sanitize_summary(title_summary) or sanitize_summary(summary)
    return ensure_pr(
        head=resolved,
        issue=issue,
        fix=issue,
        title=format_ir_pr_title(
            issue=issue,
            incident_id=page.get("incident_id") or page.get("page_id"),
        ),
        body=body,
        incident_id=page.get("incident_id") or page.get("page_id"),
        case_id=page.get("case_id"),
        runner=runner,
        gh_bin=gh_bin,
        which=which,
        update_existing=True,
        repo_root=repo_root,
        scan_base="origin/main",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ensure an open PR exists for a fix/** incident-response "
            "branch. Never merges."
        )
    )
    parser.add_argument("--head", help="fix/<slug>; default: current IR branch")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--issue", help="plain-language what broke")
    parser.add_argument("--fix", dest="fix_text", help="plain-language what changed")
    parser.add_argument("--next", dest="next_action")
    parser.add_argument("--incident-id")
    parser.add_argument("--case-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        head = args.head
        if not head:
            head = infer_ir_head(Path.cwd())
            if not head:
                raise IrEnsurePrError(
                    "no fix/** branch on HEAD; pass --head. "
                    "Branch-only is not a ship."
                )
        # The body always comes from the fix commit's IR sections; there
        # is no placeholder default. --issue only overrides the title line.
        commit = _git_stdout(
            _default_runner, ["git", "log", "-1", "--format=%B", head], Path.cwd()
        )
        try:
            summary, body = ir_pr_description.description_from_commit(
                commit, branch=head
            )
        except ir_pr_description.IrDescriptionError as exc:
            raise IrEnsurePrError(f"IR PR description invalid: {exc}") from exc
        issue = sanitize_summary(args.issue or summary)
        result = ensure_pr(
            head=head,
            issue=issue,
            fix=args.fix_text or issue,
            next_action=args.next_action,
            incident_id=args.incident_id,
            case_id=args.case_id,
            base=args.base,
            repo=args.repo,
            body=body,
            update_existing=True,
            repo_root=Path.cwd(),
            scan_base=f"origin/{args.base}",
        )
    except IrEnsurePrError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    if args.json:
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(f"{result['action']} {result['url']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
