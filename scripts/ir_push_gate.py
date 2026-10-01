"""Gate every automated push of an incident-response branch to the public repo.

Two checks, both fail closed:

1. ``GROK_PAGE_AUTOPUSH`` must be truthy in the pushing process's
   environment. Unset, empty, ``0``, ``false``, ``no`` or ``off`` means no
   push and no PR (REL-030: unset means off).
2. The commit range (added diff lines, file names, commit messages) and the
   PR title/body must carry no private identifier: IB account ids, long
   numeric Flex exec ids, dotted-hex IB exec ids, or any specific credential
   shape from the canonical scrubber (``credential_redaction``). A hit
   refuses the push; the work stays local. Nothing is redacted in place,
   because silently rewriting code or history hides what leaked.

Findings name the kind and where it was found, never the matched value, so
they are safe to log and alert on.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Mapping

from credential_redaction import find_credential_shapes

AUTOPUSH_ENV = "GROK_PAGE_AUTOPUSH"
_FALSEY = {"", "0", "false", "no", "off"}

# Shapes, not values. Tests use invented ids only.
PRIVATE_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ib_account_id", re.compile(r"(?<![A-Za-z0-9])(?:DU|U|F)\d{6,8}(?![A-Za-z0-9])")),
    (
        "ib_exec_id",
        re.compile(
            r"(?<![0-9A-Za-z.])[0-9a-f]{8}\.[0-9a-f]{8}\.\d{2}\.\d{2}(?![0-9A-Za-z])",
            re.IGNORECASE,
        ),
    ),
    # GitHub Actions run/job ids (11 digits) are exempt only inside a full
    # CI URL, which is removed before this pattern runs (_CI_URL).
    ("flex_exec_id", re.compile(r"(?<![0-9A-Za-z.])\d{10,}(?![0-9A-Za-z])")),
)
_CI_URL = re.compile(
    r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+"
    r"(?:/(?:job|attempts)/\d+)?(?![\w/])"
)
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

Runner = Callable[..., object]


class IrPushRefused(RuntimeError):
    """The push or PR must not happen. The message is safe to log."""


def autopush_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    raw = source.get(AUTOPUSH_ENV)
    return raw is not None and raw.strip().lower() not in _FALSEY


def require_autopush(env: Mapping[str, str] | None = None) -> None:
    if not autopush_enabled(env):
        raise IrPushRefused(
            f"{AUTOPUSH_ENV} is not enabled; no push and no PR "
            "(the fix stays on the local branch)"
        )


def find_private_identifiers(text: str) -> list[str]:
    """Kinds of private identifier present in ``text`` (never the values)."""
    found: list[str] = []
    for label, pattern in PRIVATE_ID_PATTERNS:
        subject = _CI_URL.sub("", text or "") if label == "flex_exec_id" else text or ""
        if pattern.search(subject):
            found.append(label)
    for label in find_credential_shapes(text or ""):
        kind = f"credential {label}"
        if kind not in found:
            found.append(kind)
    return found


def _safe_path(path: str) -> str:
    """``path`` with any private identifier in it masked, for findings."""
    if not find_private_identifiers(path):
        return path
    return f"a file path ({len(path)} chars, masked)"


def _default_runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=kwargs.get("cwd"),
        capture_output=True,
        text=True,
        timeout=kwargs.get("timeout", 120),
    )


def _git(repo: Path, argv: list[str], runner: Runner) -> str:
    try:
        proc = runner(["git", *argv], cwd=str(repo))
    except UnicodeDecodeError:
        raise IrPushRefused(
            f"cannot scan the branch: git {argv[0]} output is not text"
        ) from None
    if getattr(proc, "returncode", 1) != 0:
        raise IrPushRefused(f"cannot scan the branch: git {argv[0]} failed")
    return getattr(proc, "stdout", "") or ""


def _added_lines_by_file(patch: str) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    current = "(unknown file)"
    # split("\n"), not splitlines(): a \x1c or \u2028 inside an added line
    # must not start a new line that lacks the "+" marker.
    for line in patch.split("\n"):
        if line.startswith("+++ "):
            name = line[4:].strip()
            current = name[2:] if name.startswith("b/") else name
            files.setdefault(current, [current])  # the path itself is published
            continue
        if line.startswith("+") and not line.startswith("+++"):
            files.setdefault(current, []).append(line[1:])
    return files


def scan_commit_range(
    repo: Path,
    base: str,
    ref: str,
    *,
    runner: Runner | None = None,
) -> list[str]:
    """Findings for everything ``ref`` would publish on top of ``base``.

    Scans each commit message (subject, body and trailers) and every line
    and path any commit in the range adds. Removed lines are already public
    on the base.
    """
    run = runner or _default_runner
    repo = Path(repo)
    fork = _git(repo, ["merge-base", base, ref], run).strip()
    if not fork:
        raise IrPushRefused(f"cannot scan the branch: no merge base with {base}")
    findings: list[str] = []
    # The pushed branch name is public too.
    for kind in find_private_identifiers(ref):
        findings.append(f"{kind} in branch name")
    log = _git(repo, ["log", "--format=%H%x00%B%x1e", f"{fork}..{ref}"], run)
    for record in log.split("\x1e"):
        if not record.strip():
            continue
        sha, _, message = record.strip("\n").partition("\x00")
        if not _SHA.fullmatch(sha.strip()):
            # A message carrying the record separator would otherwise
            # smuggle text past the scan as a fake sha field.
            raise IrPushRefused("cannot scan the branch: unparseable commit log")
        for kind in find_private_identifiers(message):
            findings.append(f"{kind} in commit message {sha.strip()[:12]}")
    # Every commit's own patch, not one aggregate diff: pushing publishes
    # each intermediate blob, so content added then removed still counts.
    # --text and --no-textconv: a branch-authored .gitattributes (-diff,
    # binary, a textconv driver) must not hide what the blob publishes.
    patch = _git(
        repo,
        [
            "log", "-p", "-m", "--format=", "--no-color", "--no-ext-diff",
            "--no-textconv", "--text", "--no-renames", "--unified=0",
            f"{fork}..{ref}",
        ],
        run,
    )
    added = _added_lines_by_file(patch)
    for path, lines in added.items():
        if any("\x00" in line for line in lines):
            raise IrPushRefused(
                f"cannot scan the branch: binary content in {_safe_path(path)}"
            )
        for kind in find_private_identifiers("\n".join(lines)):
            findings.append(f"{kind} in diff of {_safe_path(path)}")
    # Paths with no content hunk (new empty files) are published too.
    names = _git(
        repo,
        [
            "log", "-m", "--format=", "--name-only", "-z", "--no-renames",
            "--diff-filter=d", f"{fork}..{ref}",
        ],
        run,
    )
    for path in (name.strip("\n") for name in names.split("\x00")):
        if path and path not in added:
            for kind in find_private_identifiers(path):
                findings.append(f"{kind} in diff of {_safe_path(path)}")
    return findings


def scan_pr_text(*, title: str | None, body: str | None) -> list[str]:
    findings = [f"{kind} in PR title" for kind in find_private_identifiers(title or "")]
    findings += [f"{kind} in PR body" for kind in find_private_identifiers(body or "")]
    return findings


def refusal_message(findings: Iterable[str]) -> str:
    items = list(findings)
    return (
        "private identifiers found; refusing to publish (the fix stays on "
        "the local branch): " + "; ".join(items[:12])
        + (f"; and {len(items) - 12} more" if len(items) > 12 else "")
    )


def check_publish(
    *,
    repo: Path | None = None,
    base: str | None = None,
    ref: str | None = None,
    title: str | None = None,
    body: str | None = None,
    runner: Runner | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Raise :class:`IrPushRefused` unless this push/PR may go public."""
    require_autopush(env)
    findings: list[str] = []
    if repo is not None and base and ref:
        findings += scan_commit_range(repo, base, ref, runner=runner)
    findings += scan_pr_text(title=title, body=body)
    if findings:
        raise IrPushRefused(refusal_message(findings))
