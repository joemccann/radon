#!/usr/bin/env python3
"""Pick up grok's incident-response fix branches from the VPS clone.

The Grok page responder runs `grok --always-approve` over untrusted page
text. A GitHub credential on that host can merge to main, because pushing a
branch and merging a pull request need the same permission -- so the VPS
holds no credential at all. The responder commits to `fix/<slug>` locally;
this job, on the Mac mini, fetches those branches over ssh, pushes them and
opens the PR. It never merges: Joe merges after CI is green.

The branch content is still attacker-influenced, so pickup refuses anything
it cannot describe:

  * refs outside `fix/<slug>` (no refspec, option or path tricks);
  * a diff touching `.github/` -- a PR-triggered workflow runs from the PR
    head, so that would execute attacker-authored CI in this repository;
  * a branch that does not descend from origin/main, or exceeds the commit
    cap.

Fetching from a hostile repository is a supported git operation; nothing
here executes code out of the fetched tree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import ir_ensure_pr
import ir_pr_description

DEFAULT_SOURCE = os.environ.get(
    "RADON_GROK_FIX_SOURCE",
    "ssh://radon@ib-gateway/home/radon/radon-page-responder",
)
PICKUP_NAMESPACE = "refs/remotes/grok-vps/"
DEFAULT_MAX_COMMITS = 20
DEFAULT_BASE = "origin/main"
DEFAULT_ORIGIN = "origin"
REFUSED_PATH_PREFIXES = (".github/",)
# One slug segment, no dots-only names, no whitespace, no leading dash.
_PICKUP_BRANCH = re.compile(r"^fix/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

Runner = Callable[..., subprocess.CompletedProcess]


def _default_runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=kwargs.get("cwd"),
        capture_output=True,
        text=True,
        timeout=kwargs.get("timeout", 120),
    )


def is_pickup_branch(name: str) -> bool:
    """True for a `fix/<slug>` branch this job is allowed to carry."""
    if not name or ".." in name:
        return False
    return bool(_PICKUP_BRANCH.match(name))


def _git(
    repo: Path, argv: list[str], *, runner: Runner
) -> subprocess.CompletedProcess:
    return runner(["git", *argv], cwd=str(repo))


def _stdout(proc: subprocess.CompletedProcess) -> str:
    return (getattr(proc, "stdout", "") or "").strip()


def list_source_branches(
    repo: Path, source: str, *, runner: Runner
) -> list[str]:
    """`fix/<slug>` heads on the VPS clone, newest listing order preserved."""
    proc = _git(
        repo, ["ls-remote", "--heads", source, "refs/heads/fix/*"], runner=runner
    )
    if getattr(proc, "returncode", 1) != 0:
        raise PickupError(f"cannot list {source}: {_stdout(proc)}")
    names: list[str] = []
    for line in _stdout(proc).splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        name = parts[1].removeprefix("refs/heads/")
        if is_pickup_branch(name) and name not in names:
            names.append(name)
    return names


class PickupError(RuntimeError):
    """Pickup cannot run at all (bad source, git failure)."""


def _origin_head(
    repo: Path, name: str, *, origin: str, runner: Runner
) -> str | None:
    proc = _git(
        repo,
        ["ls-remote", "--heads", origin, f"refs/heads/{name}"],
        runner=runner,
    )
    if getattr(proc, "returncode", 1) != 0:
        raise PickupError(f"cannot inspect origin head for {name}")
    raw = _stdout(proc)
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 2 or parts[1] != f"refs/heads/{name}" or not re.fullmatch(r"[0-9a-f]{40,64}", parts[0]):
        raise PickupError(f"invalid origin head response for {name}")
    return parts[0]


def refusal_reason(
    repo: Path,
    ref: str,
    *,
    base: str = DEFAULT_BASE,
    max_commits: int = DEFAULT_MAX_COMMITS,
    runner: Runner,
) -> Optional[str]:
    """Why this fetched ref must not be pushed, or None when it may be."""
    merge_base_proc = _git(repo, ["merge-base", base, ref], runner=runner)
    if getattr(merge_base_proc, "returncode", 1) != 0 or not _stdout(merge_base_proc):
        return f"does not descend from {base}"
    fork_point = _stdout(merge_base_proc)

    count_proc = _git(
        repo, ["rev-list", "--count", f"{fork_point}..{ref}"], runner=runner
    )
    raw_count = _stdout(count_proc)
    if getattr(count_proc, "returncode", 1) != 0 or not raw_count.isdigit():
        return "cannot count commits on the branch"
    commits = int(raw_count)
    if commits == 0:
        return "no commits ahead of the base"
    if commits > max_commits:
        return f"{commits} commits exceeds the cap of {max_commits}"

    diff_proc = _git(
        repo, ["diff", "--name-only", f"{fork_point}..{ref}"], runner=runner
    )
    if getattr(diff_proc, "returncode", 1) != 0:
        return "cannot list changed paths"
    for path in _stdout(diff_proc).splitlines():
        if path.startswith(REFUSED_PATH_PREFIXES):
            return f"changes {path}: .github/ is never picked up automatically"
    return None


def _ensure_pr_default(**kwargs) -> dict:
    return ir_ensure_pr.ensure_pr(include_terminal=True, **kwargs)


def default_lookup_page(
    *, page_id: str | None = None, result_needle: str | None = None
) -> dict | None:
    """Best-effort Turso read. Pickup still works when Turso is unreachable."""
    try:
        from watchdog import pages as pages_mod
    except ImportError:
        try:
            from scripts.watchdog import pages as pages_mod
        except ImportError:
            return None
    try:
        if page_id:
            return pages_mod.get_page(page_id)
        if result_needle:
            return pages_mod.find_page_by_result(result_needle)
    except Exception:  # noqa: BLE001 — enrichment only
        return None
    return None


def default_ci_urls(head: str, *, runner: Runner) -> list[str]:
    proc = runner([
        "gh", "run", "list",
        "--repo", ir_ensure_pr.DEFAULT_REPO,
        "--branch", head,
        "--limit", "8",
        "--json", "url,databaseId,name,conclusion",
    ])
    if getattr(proc, "returncode", 1) != 0:
        return []
    try:
        rows = json.loads(getattr(proc, "stdout", "") or "[]")
    except ValueError:
        return []
    urls: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if isinstance(url, str) and url.startswith("http") and url not in urls:
            urls.append(url)
    return urls


def alert_invalid_description(
    branch: str,
    reason: str,
    *,
    alerter: Optional[Callable[[str, str], None]] = None,
) -> None:
    message = f"grok fix pickup: refusing {branch}: {reason}"
    print(message, file=sys.stderr)
    if alerter:
        alerter(branch, reason)
        return
    try:
        from watchdog import notify
    except ImportError:
        return
    creds = notify._pushover_creds()
    if not creds:
        return
    user, token = creds
    notify._post_pushover(
        notify.build_pushover_payload(
            user=user,
            token=token,
            title="radon grok pickup: IR body refused",
            message=message[:900],
            severity=None,
        )
    )


def build_pickup_pr_kwargs(
    repo: Path,
    ref: str,
    branch: str,
    *,
    runner: Runner,
    lookup_page: Optional[Callable[..., dict | None]] = None,
    list_ci_urls: Optional[Callable[[str], list[str]]] = None,
) -> dict:
    """Title/body from the grok commit, page row, and CI links."""
    message = _stdout(_git(repo, ["log", "-1", "--format=%B", ref], runner=runner))
    sha = _stdout(_git(repo, ["rev-parse", ref], runner=runner))
    finder = lookup_page or default_lookup_page
    page = None
    page_id = ir_pr_description.extract_page_id(message)
    if page_id:
        page = finder(page_id=page_id)
    if page is None and sha:
        page = finder(result_needle=sha[:12])
    ci_urls = list_ci_urls(branch) if list_ci_urls else default_ci_urls(branch, runner=runner)
    title, body = ir_pr_description.description_from_commit(
        message, branch=branch, page=page, ci_urls=ci_urls
    )
    incident_id = (page or {}).get("page_id")
    return {
        "head": branch,
        "issue": title,
        "fix": title,
        "title": ir_ensure_pr.format_ir_pr_title(issue=title, incident_id=incident_id),
        "body": body,
        "incident_id": incident_id,
        "update_existing": True,
    }


def pickup_once(
    repo_root: Path,
    *,
    source: str = DEFAULT_SOURCE,
    ensure_pr: Optional[Callable[..., dict]] = None,
    runner: Optional[Runner] = None,
    base: str = DEFAULT_BASE,
    origin: str = DEFAULT_ORIGIN,
    max_commits: int = DEFAULT_MAX_COMMITS,
    lookup_page: Optional[Callable[..., dict | None]] = None,
    list_ci_urls: Optional[Callable[[str], list[str]]] = None,
    alerter: Optional[Callable[[str, str], None]] = None,
) -> list[dict]:
    """Fetch, gate, push and open a PR for each new grok fix branch."""
    run = runner or _default_runner
    repo_root = Path(repo_root)
    open_pr = ensure_pr or _ensure_pr_default

    _git(repo_root, ["fetch", "--quiet", origin], runner=run)

    results: list[dict] = []
    for name in list_source_branches(repo_root, source, runner=run):
        origin_head = _origin_head(repo_root, name, origin=origin, runner=run)

        local_ref = f"{PICKUP_NAMESPACE}{name}"
        fetched = _git(
            repo_root,
            ["fetch", "--no-tags", "--quiet", source,
             f"+refs/heads/{name}:{local_ref}"],
            runner=run,
        )
        if getattr(fetched, "returncode", 1) != 0:
            results.append({
                "branch": name,
                "action": "refused",
                "reason": f"fetch failed: {_stdout(fetched)}",
            })
            continue

        if origin_head:
            fetched_head = _git(repo_root, ["rev-parse", local_ref], runner=run)
            if getattr(fetched_head, "returncode", 1) != 0 or _stdout(fetched_head) != origin_head:
                results.append({"branch": name, "action": "refused", "reason": "origin head differs from the source head"})
                continue

        reason = refusal_reason(
            repo_root, local_ref, base=base, max_commits=max_commits, runner=run
        )
        # A previously pushed branch may now be merged. Preserve its PR's
        # terminal disposition instead of creating another review request.
        if reason and not (origin_head and reason == "no commits ahead of the base"):
            results.append({"branch": name, "action": "refused", "reason": reason})
            continue

        try:
            pr_kwargs = build_pickup_pr_kwargs(
                repo_root,
                local_ref,
                name,
                runner=run,
                lookup_page=lookup_page,
                list_ci_urls=list_ci_urls,
            )
        except ir_pr_description.IrDescriptionError as exc:
            alert_invalid_description(name, str(exc), alerter=alerter)
            results.append({
                "branch": name,
                "action": "refused",
                "reason": f"IR description: {exc}",
            })
            continue

        if not origin_head:
            pushed = _git(
                repo_root,
                ["push", "--quiet", origin, f"{local_ref}:refs/heads/{name}"],
                runner=run,
            )
            if getattr(pushed, "returncode", 1) != 0:
                results.append({
                    "branch": name,
                    "action": "refused",
                    "reason": f"push failed: {_stdout(pushed)}",
                })
                continue

        pr = open_pr(**pr_kwargs)
        if not isinstance(pr, dict) or not pr.get("url"):
            raise PickupError(f"no confirmed PR URL for {name}")
        results.append({
            "branch": name,
            "action": pr["action"] if pr.get("action") in {"closed", "merged"} else "picked_up",
            "url": (pr or {}).get("url"),
        })
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Push grok's fix/** branches from the VPS clone and open their "
            "PRs. Never merges."
        )
    )
    parser.add_argument("--repo", default=".", help="local clone to work in")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--max-commits", type=int, default=DEFAULT_MAX_COMMITS)
    args = parser.parse_args(argv)

    try:
        results = pickup_once(
            Path(args.repo).resolve(),
            source=args.source,
            max_commits=args.max_commits,
        )
    except (PickupError, ir_ensure_pr.IrEnsurePrError) as exc:
        print(f"grok fix pickup failed: {exc}", file=sys.stderr)
        return 1
    for row in results:
        print(json.dumps(row, sort_keys=True))
    if any(
        str(row.get("reason") or "").startswith("IR description:")
        for row in results
    ):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
