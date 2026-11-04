#!/usr/bin/env python3
"""Scoped pre-push check: run, before a push, the tests a PR most often goes red on.

`.githooks/pre-push` execs this file with git's ref lines on stdin. The root
package.json "prepare" script (every `bun install`) runs `--install`, which
writes a small shim into the git hooks dir that runs the pushing checkout's
.githooks/pre-push. It deliberately does NOT set core.hooksPath: that moves
`git rev-parse --git-path hooks` onto the tracked .githooks/ dir, and
scripts/grok_page_responder.py install_push_guard writes its fix/*-only push
guard to exactly that path every cycle.

Selection, over the files changed from merge-base(origin/main, HEAD):
  - each changed python test file, and scripts/**/test_<stem>*.py for each
    changed scripts/**/<stem>.py;
  - the CI pins when .github/ or scripts/ci/ changed;
  - path_filter's cross-tree contracts for each changed path (the e2e
    curation ledger for a web/e2e spec, the pytest readers of a doc);
  - ALWAYS_TESTS on any change: repo-wide guards a code-only change breaks.
Before the tests, on the same changed list and only when something relevant
changed: Oxlint on the four middleware-graph files (the Edge node-builtin
import ban; Oxlint has no selector syntax rule, so the ban is the same
static import list), then `tsc --noEmit` on the changed `web/` and
`lib/tools/` sources only. A project-wide `tsc` is about 15s here; the
scoped project stays under 10s. Either check is skipped, not failed, when
its binary is absent. Then pytest (cloud/tests in its own run: its conftest
collides with scripts/tests on `tests.conftest`), then `vitest related` on
the changed web sources. Never concurrently.

Root `bun install` runs `prepare`, which is `--install`. That is how a
checkout gets the shim. An image layer that only has package.json (no
`scripts/ci/pre_push.py`, no `.git`) stays a no-op. A cloud agent image sets
`core.hooksPath` to a dispatcher directory, so git never reads `.git/hooks`.
When that directory has `.dispatcher`, `--install` links `pre-push` to it
and writes `pre-push.cursor.radon`, which the dispatcher runs. A hooksPath
that is not a directory (or already has a foreign `pre-push`) is left alone.

Only a ref that is checked out (its commit is HEAD) is checked: the tests run
against the working tree. Skip once with `git push --no-verify`, always with
RADON_SKIP_PRE_PUSH=1. CI never runs it.
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

try:
    from ci.path_filter import changed_paths, classify, select_gates
except ImportError:  # run as a script: scripts/ci is sys.path[0]
    from path_filter import changed_paths, classify, select_gates

REPO = Path(__file__).resolve().parents[2]

# First-attempt reds in 150 CI runs to 2026-11-01: 22 in test jobs. Of the 8
# scripts-df reds sampled, 4 were the docs ownership contract (a mapped CODE
# path changed without its owner doc, so a .md-only trigger misses it) and 4
# the e2e curation ledger. The subscriptions-only scan reddened scripts-rs on
# a scripts/ change. Both guards together take about 2s locally.
ALWAYS_TESTS = (
    "scripts/tests/test_docs_contract.py",
    "scripts/tests/test_subscriptions_only_policy.py",
)
CI_PIN_TRIGGERS = (".github/", "scripts/ci/")
CI_PIN_GLOB = "test_ci_*.py"
CI_PINS = ("scripts/tests/test_path_filter.py", "cloud/tests/test_app_images.py")
PY_TEST_ROOTS = ("scripts/", "tests/", "cloud/tests/")
VITEST_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
TYPECHECK_SUFFIXES = (".ts", ".tsx")
# web/tsconfig.json excludes these. Passing one to tsc checks it anyway.
WEB_TYPECHECK_SKIP = ("tests/", "e2e/", "e2e-setup-gate/", "tester-army/", ".next")
# Same four files as web/eslint.config.mjs and the CI perimeter eslint step.
# Paths are repo-relative; Oxlint runs with cwd=web/.
PERIMETER_FILES = (
    "web/middleware.ts",
    "web/lib/apiContracts.ts",
    "web/lib/probeAuth.ts",
    "web/lib/sameOrigin.ts",
)
OXLINTRC = "oxlint.perimeter.json"
NOT_STEMS = frozenset({"__init__", "conftest"})
SKIP_HINT = (
    "pre-push: blocked. Fix the failures above, or skip once with "
    "`git push --no-verify` (every push: RADON_SKIP_PRE_PUSH=1)."
)
SHIM_MARKER = "# radon pre-push shim"
SHIM = f"""#!/bin/sh
{SHIM_MARKER}: written by `python3 scripts/ci/pre_push.py --install` (the root
# package.json "prepare" script). Runs the pushing checkout's .githooks/pre-push;
# a branch without one is not checked.
[ -f .githooks/pre-push ] || exit 0
exec sh .githooks/pre-push "$@"
"""

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class PushedRef:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str

    @property
    def deleted(self) -> bool:
        return set(self.local_sha) <= {"0"}


@dataclass(frozen=True)
class Selection:
    python_tests: tuple[str, ...]
    cloud_tests: tuple[str, ...]
    vitest_files: tuple[str, ...]
    lint_files: tuple[str, ...]
    typecheck_files: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not (
            self.python_tests or self.cloud_tests or self.vitest_files
            or self.lint_files or self.typecheck_files
        )


def parse_push_lines(text: str) -> list[PushedRef]:
    """`<local ref> <local sha> <remote ref> <remote sha>` per line; others ignored."""
    refs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            refs.append(PushedRef(*parts))
    return refs


def _is_python_test(path: str) -> bool:
    name = posixpath.basename(path)
    return path.startswith(PY_TEST_ROOTS) and name.startswith("test_") and name.endswith(".py")


def _tests_under(root: Path, tree: str, pattern: str) -> list[str]:
    base = root / tree
    if not base.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in base.rglob(pattern)
        if "node_modules" not in path.parts
    )


def select(paths: Iterable[str], root: Path = REPO) -> Selection:
    paths = list(paths)
    chosen: set[str] = set()
    vitest: set[str] = set()
    scripts_tests: list[str] | None = None
    for path in paths:
        name = posixpath.basename(path)
        exists = (root / path).is_file()
        if _is_python_test(path):
            if exists:
                chosen.add(path)
        elif path.startswith("scripts/") and name.endswith(".py") and name[:-3] not in NOT_STEMS:
            if scripts_tests is None:
                scripts_tests = _tests_under(root, "scripts", "test_*.py")
            prefix = f"test_{name[:-3]}"
            chosen.update(t for t in scripts_tests if posixpath.basename(t).startswith(prefix))
        if path.startswith(CI_PIN_TRIGGERS):
            chosen.update(p.relative_to(root).as_posix() for p in (root / "scripts/tests").glob(CI_PIN_GLOB))
            chosen.update(p for p in CI_PINS if (root / p).is_file())
        chosen.update(select_gates([path]).contract_tests)
        if exists and path.endswith(VITEST_SUFFIXES) and classify([path])[1]:
            vitest.add(path)
    if paths:
        chosen.update(t for t in ALWAYS_TESTS if (root / t).is_file())
    modules = {t for t in chosen if "::" not in t}
    chosen = {t for t in chosen if "::" not in t or t.split("::", 1)[0] not in modules}
    web_tc, tools_tc = _typecheck_groups(paths, root)
    return Selection(
        python_tests=tuple(sorted(t for t in chosen if not t.startswith("cloud/"))),
        cloud_tests=tuple(sorted(t for t in chosen if t.startswith("cloud/"))),
        vitest_files=tuple(sorted(vitest)),
        lint_files=tuple(sorted(
            p for p in dict.fromkeys(paths) if p in PERIMETER_FILES and (root / p).is_file()
        )),
        typecheck_files=tuple(sorted(set(web_tc + tools_tc))),
    )


def _bin(root: Path, name: str) -> Path | None:
    for rel in (f"web/node_modules/.bin/{name}", f"node_modules/.bin/{name}"):
        path = root / rel
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def _typecheck_groups(paths: Iterable[str], root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Changed sources the web and lib/tools tsconfigs would actually check."""
    web: list[str] = []
    tools: list[str] = []
    for path in paths:
        if not path.endswith(TYPECHECK_SUFFIXES) or not (root / path).is_file():
            continue
        if path.startswith("web/"):
            rel = path[len("web/"):]
            if rel.startswith(WEB_TYPECHECK_SKIP):
                continue
            web.append(path)
        elif path.startswith("lib/tools/") and not path.startswith("lib/tools/__tests__/"):
            tools.append(path)
    return tuple(sorted(set(web))), tuple(sorted(set(tools)))


def _write_tsconfig(dest: Path, base: Path, files: list[Path]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "extends": os.path.relpath(base, dest.parent),
        "include": [],
        "files": [os.path.relpath(path, dest.parent) for path in files],
        "compilerOptions": {"noEmit": True, "incremental": False},
    }
    dest.write_text(json.dumps(payload))


def _scrubbed(env: dict[str, str]) -> dict[str, str]:
    """Drop GIT_*: a push from a worktree exports GIT_DIR to the hook, and a
    test that runs `git init` / `git commit` in a temp dir would then write
    to the pushing repository instead."""
    return {key: value for key, value in env.items() if not key.startswith("GIT_")}


def _git(root: Path, *args: str) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        env=_scrubbed(dict(os.environ)),
    )
    return done.stdout.strip() if done.returncode == 0 else None


def merge_base(head: str, root: Path = REPO) -> str | None:
    """Fork point of `head` from origin/main (else main). Never fetches."""
    for ref in ("origin/main", "main"):
        if _git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}") is None:
            continue
        base = _git(root, "merge-base", ref, head)
        if base:
            return base
    return None


def python_for(root: Path = REPO) -> str:
    """The main checkout's .venv python (shared by every worktree), else python3."""
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        venv = Path(common).parent / ".venv" / "bin" / "python"
        if venv.is_file() and os.access(venv, os.X_OK):
            return str(venv)
    return "python3"


def _write_shim(hook: Path) -> int:
    """Write the shim. Never fails, never replaces a foreign hook."""
    try:
        if hook.is_symlink():
            print(f"pre-push: {hook} is a symlink; shim not installed")
            return 0
        if hook.exists():
            if SHIM_MARKER not in hook.read_text(errors="ignore"):
                print(f"pre-push: {hook} already exists; shim not installed")
                return 0
        else:
            print(f"pre-push: installed {hook}")
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(SHIM)
        hook.chmod(0o755)
    except OSError as error:
        print(f"pre-push: shim not installed ({error})")
    return 0


def _install_dispatcher(root: Path, hook_dir: Path) -> int:
    """Git is reading `hook_dir`, not `.git/hooks`. Link pre-push at the
    dispatcher and give it a companion script to run. Drop our own shim from
    `.git/hooks` so the dispatcher does not run the check twice."""
    link = hook_dir / "pre-push"
    if link.is_symlink():
        if Path(link.readlink()).name != ".dispatcher":
            print(f"pre-push: {link} is a symlink; shim not installed")
            return 0
    elif link.exists():
        print(f"pre-push: {link} already exists; shim not installed")
        return 0
    else:
        try:
            link.symlink_to(".dispatcher")
            print(f"pre-push: linked {link} -> .dispatcher")
        except OSError as error:
            print(f"pre-push: shim not installed ({error})")
            return 0
    # --git-path hooks follows core.hooksPath, which is the dispatcher dir.
    # The copy that would run twice is the one in the common git dir.
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        old = Path(common) / "hooks" / "pre-push"
        try:
            if old.is_file() and not old.is_symlink() and SHIM_MARKER in old.read_text(errors="ignore"):
                old.unlink()
        except OSError:
            pass
    return _write_shim(hook_dir / "pre-push.cursor.radon")


def install(root: Path = REPO) -> int:
    """Write the shim where git will run it. Never fails, never replaces a foreign hook."""
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return 0
    configured = _git(root, "config", "--get", "core.hooksPath")
    if configured:
        hook_dir = Path(configured)
        if hook_dir.is_dir() and (hook_dir / ".dispatcher").is_file():
            return _install_dispatcher(root, hook_dir)
        print(f"pre-push: core.hooksPath is {configured}; shim not installed")
        return 0
    hooks = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks")
    if not hooks:
        return 0
    return _write_shim(Path(hooks) / "pre-push")


def _report(paths: list[str], base: str, sel: Selection) -> None:
    python, web = classify(paths)
    print(
        f"pre-push: {len(paths)} changed files vs {base[:9]} "
        f"(CI gates armed: python={python} web={web})"
    )
    for label, items in (
        ("oxlint", sel.lint_files),
        ("tsc", sel.typecheck_files),
        ("pytest", sel.python_tests),
        ("pytest cloud", sel.cloud_tests),
        ("vitest related", sel.vitest_files),
    ):
        if items:
            print(f"pre-push: {label}: {' '.join(items)}")
    # git hands the hook a pipe, not a tty: flush before the children write.
    sys.stdout.flush()


def _tsc(
    root: Path,
    tsc: Path,
    files: tuple[str, ...],
    base: Path,
    label: str,
    env: dict[str, str],
    run: Runner,
) -> bool:
    """True when tsc failed. The project lists only `files`, so a one-file
    change does not typecheck the rest of web/."""
    dest = root / "web" / "node_modules" / ".cache" / "radon-pre-push" / f"tsconfig.{label}.json"
    _write_tsconfig(dest, base, [root / path for path in files])
    try:
        done = run(
            [str(tsc), "--noEmit", "--pretty", "false", "-p", str(dest)],
            cwd=root,
            env=env,
        )
        return done.returncode != 0
    finally:
        dest.unlink(missing_ok=True)


def _static(root: Path, sel: Selection, env: dict[str, str], run: Runner) -> list[str]:
    failed: list[str] = []
    if sel.lint_files:
        ox = _bin(root, "oxlint")
        if ox is None:
            print("pre-push: oxlint skipped (no node_modules/.bin/oxlint)")
        else:
            done = run(
                [str(ox), "-c", OXLINTRC, *(path[len("web/"):] for path in sel.lint_files)],
                cwd=root / "web",
                env=env,
            )
            if done.returncode != 0:
                failed.append("oxlint")
    web, tools = _typecheck_groups(sel.typecheck_files, root)
    if web or tools:
        tsc = _bin(root, "tsc")
        if tsc is None:
            print("pre-push: tsc skipped (no node_modules/.bin/tsc)")
        else:
            bad = False
            if web:
                bad = _tsc(root, tsc, web, root / "web" / "tsconfig.json", "web", env, run) or bad
            if tools:
                bad = _tsc(root, tsc, tools, root / "lib" / "tsconfig.json", "tools", env, run) or bad
            if bad:
                failed.append("tsc")
    return failed


def _check(root: Path, base: str, sel: Selection, env: dict[str, str], run: Runner) -> list[str]:
    child = {**_scrubbed(env), "DOCS_CONTRACT_BASE": base}
    failed = _static(root, sel, child, run)
    # Lint and typecheck are the fast gate. A red one stops the hook before
    # pytest and vitest, which are the slow part.
    if failed:
        return failed
    if sel.python_tests or sel.cloud_tests:
        python = python_for(root)
        probe = run([python, "-c", "import pytest, xdist"], cwd=root, env=child, capture_output=True)
        if probe.returncode != 0:
            print(f"pre-push: pytest skipped ({python} lacks pytest or pytest-xdist)", flush=True)
        else:
            for label, targets in (("pytest", sel.python_tests), ("pytest cloud", sel.cloud_tests)):
                if not targets:
                    continue
                done = run(
                    [python, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider",
                     "-n", "auto", "--dist", "loadfile"],
                    cwd=root,
                    env=child,
                )
                # 5 = nothing collected (e.g. a manual harness named test_*.py).
                if done.returncode not in (0, 5):
                    failed.append(label)
    if sel.vitest_files:
        if not (root / "node_modules" / ".bin" / "vitest").exists() or not shutil.which("bunx"):
            print("pre-push: vitest skipped (no node_modules/.bin/vitest or bunx in this checkout)")
        else:
            done = run(
                ["bunx", "vitest", "related", "--run", "--config", "vitest.config.ts",
                 "--passWithNoTests", *sel.vitest_files],
                cwd=root,
                env=child,
            )
            if done.returncode != 0:
                failed.append("vitest")
    return failed


def main(
    argv: list[str] | None = None,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
    root: Path = REPO,
    run: Runner = subprocess.run,
) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = dict(os.environ) if env is None else env
    if "--install" in argv:
        return install(root)
    if env.get("RADON_SKIP_PRE_PUSH") == "1":
        print("pre-push: skipped (RADON_SKIP_PRE_PUSH=1)")
        return 0
    if env.get("CI"):
        return 0
    started = time.monotonic()
    text = sys.stdin.read() if stdin is None else stdin
    pushed = [ref for ref in parse_push_lines(text) if not ref.deleted]
    head = _git(root, "rev-parse", "HEAD") if pushed else None
    if head is None:
        return 0
    checked = False
    for ref in pushed:
        if _git(root, "rev-parse", f"{ref.local_sha}^{{commit}}") == head:
            checked = True
        else:
            print(f"pre-push: {ref.local_ref} is not checked out here; not checked")
    if not checked:
        return 0
    base = merge_base(head, root)
    if base is None:
        print("pre-push: neither origin/main nor main resolves; nothing checked")
        return 0
    paths = changed_paths(base, head, cwd=root)
    sel = select(paths, root)
    if sel.empty:
        print(f"pre-push: nothing selected ({len(paths)} changed files)")
        return 0
    _report(paths, base, sel)
    failed = _check(root, base, sel, env, run)
    elapsed = time.monotonic() - started
    if failed:
        print(f"pre-push: {', '.join(failed)} failed in {elapsed:.1f}s", file=sys.stderr)
        print(SKIP_HINT, file=sys.stderr)
        return 1
    print(f"pre-push: passed in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
