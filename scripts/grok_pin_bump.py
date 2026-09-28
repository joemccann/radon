"""Weekly check for a newer stable Grok CLI or default model.

Installed disabled. Never touches the live ``~/.local/bin/grok`` binary:
a candidate is installed under a scratch GROK_HOME, smoked, and only then
committed as a ``fix/grok-pin-*`` branch for Mac mini pickup to open.
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

import grok_pin
import ir_pr_description
from grok_page_responder import parse_grok_result

Runner = Callable[..., object]
SMOKE_PROMPT = """Dry-run smoke for a Grok pin candidate. Do not edit files.
Do not push. Do not merge.

Write a markdown body with these sections, each with two real sentences
about this smoke check itself (not TODO, not a branch name):
## What broke
## Root cause
## What changed
## How it was verified
## Risk and rollback
## Still open

End with exactly one line:
RESULT: stand_down | grok pin smoke returned a structured IR summary
"""


class GrokPinBumpError(RuntimeError):
    """Bump job cannot decide or cannot write the pin PR branch."""


def _default_runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=kwargs.get("cwd"),
        env=kwargs.get("env"),
        capture_output=True,
        text=True,
        timeout=kwargs.get("timeout", 120),
        input=kwargs.get("input"),
    )


def parse_update_check(stdout: str) -> dict:
    """Accept grok update --check --json, with a text fallback."""
    text = (stdout or "").strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            current = (
                payload.get("current")
                or payload.get("installed")
                or payload.get("version")
            )
            stable = payload.get("stable")
            if isinstance(stable, dict):
                stable = stable.get("version")
            latest = (
                payload.get("latest")
                or payload.get("latest_stable")
                or stable
            )
            return {
                "current": str(current or ""),
                "latest": str(latest or ""),
            }
    versions = re.findall(r"\b(\d+\.\d+\.\d+)\b", text)
    current = versions[0] if versions else ""
    latest = versions[1] if len(versions) > 1 else current
    return {"current": current, "latest": latest}


def _cmp_version(left: str, right: str) -> int:
    def parts(value: str) -> tuple[int, ...]:
        bits = value.split(".")
        if len(bits) != 3 or not all(bit.isdigit() for bit in bits):
            raise GrokPinBumpError(f"not a dotted version: {value!r}")
        return tuple(int(bit) for bit in bits)

    l, r = parts(left), parts(right)
    return (l > r) - (l < r)


def decide_bump(
    pin: grok_pin.GrokPin,
    *,
    update_check: dict,
    models_stdout: str,
) -> dict | None:
    """Return a candidate dict, or None when the pin is still current."""
    default, available = grok_pin.parse_models_listing(models_stdout)
    latest_cli = (update_check.get("latest") or "").strip()
    newer_cli = bool(latest_cli) and _cmp_version(latest_cli, pin.cli_version) > 0
    newer_model = bool(default) and default != pin.model and default in available
    if not newer_cli and not newer_model:
        return None
    return {
        "model": default if newer_model else pin.model,
        "cli_version": latest_cli if newer_cli else pin.cli_version,
        "reasoning_effort": pin.reasoning_effort,
        "reason": (
            (f"CLI {pin.cli_version} -> {latest_cli}" if newer_cli else "")
            + ("; " if newer_cli and newer_model else "")
            + (f"default model {pin.model} -> {default}" if newer_model else "")
        ),
    }


def install_side_cli(
    version: str,
    dest: Path,
    *,
    grok_bin: str,
    runner: Runner,
) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GROK_HOME"] = str(dest)
    env["HOME"] = str(dest)
    proc = runner(
        [grok_bin, "update", "--version", version, "--no-auto-update"],
        env=env,
        cwd=str(dest),
        timeout=180,
    )
    if getattr(proc, "returncode", 1) != 0:
        raise GrokPinBumpError(
            f"side install of grok {version} failed: "
            f"{(getattr(proc, 'stderr', '') or '')[:300]}"
        )
    for candidate in (
        dest / "bin" / "grok",
        dest / ".grok" / "bin" / "grok",
        dest / "grok",
    ):
        if candidate.exists():
            return candidate
    return Path(grok_bin)


def run_smoke(
    *,
    grok_bin: str,
    model: str,
    reasoning_effort: str,
    scratch: Path,
    runner: Runner,
    prompt: str = SMOKE_PROMPT,
) -> tuple[str, str]:
    """Canned dry-run. Must return RESULT + a validator-passing body."""
    scratch.mkdir(parents=True, exist_ok=True)
    prompt_path = scratch / "smoke.prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    proc = runner(
        [
            grok_bin,
            "--prompt-file", str(prompt_path),
            "--cwd", str(scratch),
            "--always-approve",
            "--no-auto-update",
            "--no-plan",
            "-m", model,
            "--reasoning-effort", reasoning_effort,
            "--output-format", "json",
        ],
        cwd=str(scratch),
        timeout=600,
    )
    if getattr(proc, "returncode", 1) != 0:
        raise GrokPinBumpError(
            f"smoke grok exit {getattr(proc, 'returncode', 1)}: "
            f"{(getattr(proc, 'stderr', '') or '')[:300]}"
        )
    stdout = getattr(proc, "stdout", "") or ""
    disposition, summary = parse_grok_result(stdout)
    if disposition not in {"stand_down", "ops_only", "code_fix"}:
        raise GrokPinBumpError(f"smoke RESULT was {disposition}: {summary}")
    ir_pr_description.validate_ir_description(stdout, branch="fix/grok-pin-smoke")
    return disposition, summary


def _git(repo: Path, argv: list[str], *, runner: Runner) -> subprocess.CompletedProcess:
    return runner(["git", *argv], cwd=str(repo))


def commit_pin_bump(
    repo: Path,
    pin_path: Path,
    new_pin: grok_pin.GrokPin,
    *,
    reason: str,
    smoke_summary: str,
    runner: Runner,
) -> str:
    """Write the pin and commit on ``fix/grok-pin-<cli>-<model>``."""
    slug_model = re.sub(r"[^A-Za-z0-9._-]+", "-", new_pin.model)[:40]
    slug_cli = new_pin.cli_version.replace(".", "-")
    branch = f"fix/grok-pin-{slug_cli}-{slug_model}"
    body = (
        f"chore: pin grok {new_pin.model} / CLI {new_pin.cli_version}\n\n"
        "## What broke\n"
        "The weekly grok pin check found a newer stable CLI or default model "
        f"than config/grok_pin.json ({reason}). The live responder stays on "
        "the current pin until this branch is reviewed.\n\n"
        "## Root cause\n"
        "xAI ships CLI and default-model moves without a repo pin, so the "
        "VPS default drifted until this file existed.\n\n"
        "## What changed\n"
        f"- {pin_path.relative_to(repo) if pin_path.is_relative_to(repo) else pin_path}: "
        f"model {new_pin.model}, CLI {new_pin.cli_version}; last-known-good "
        f"keeps {new_pin.lkg_model} / {new_pin.lkg_cli_version}.\n\n"
        "## How it was verified\n"
        f"Side-installed the candidate and ran the canned dry-run smoke. "
        f"RESULT summary: {smoke_summary}\n\n"
        "## Risk and rollback\n"
        "The live VPS binary is untouched. Rollback: restore last-known-good "
        "in config/grok_pin.json or revert this commit. Keep --no-auto-update.\n\n"
        "## Still open\n"
        "Joe reviews the smoke output and merges. The weekly timer stays disabled "
        "until that enable is an explicit operator action.\n"
    )
    ir_pr_description.validate_ir_description(body, branch=branch)
    grok_pin.write_pin(pin_path, new_pin)
    for argv in (
        ["checkout", "-B", branch],
        ["add", str(pin_path)],
        ["commit", "-m", body],
    ):
        proc = _git(repo, argv, runner=runner)
        if getattr(proc, "returncode", 1) != 0:
            raise GrokPinBumpError(
                f"git {' '.join(argv)} failed: "
                f"{(getattr(proc, 'stderr', '') or getattr(proc, 'stdout', '') or '')[:300]}"
            )
    return branch


def run_bump(
    repo: Path,
    *,
    grok_bin: str,
    pin_path: Path | None = None,
    scratch: Path | None = None,
    runner: Optional[Runner] = None,
    alerter: Optional[Callable[[str], None]] = None,
) -> dict:
    run = runner or _default_runner
    pin = grok_pin.load_pin(pin_path)
    check_proc = run([grok_bin, "update", "--check", "--json"])
    if getattr(check_proc, "returncode", 1) != 0:
        raise GrokPinBumpError("grok update --check failed")
    models_proc = run([grok_bin, "models"])
    if getattr(models_proc, "returncode", 1) != 0:
        raise GrokPinBumpError("grok models failed")
    candidate = decide_bump(
        pin,
        update_check=parse_update_check(getattr(check_proc, "stdout", "") or ""),
        models_stdout=getattr(models_proc, "stdout", "") or "",
    )
    if candidate is None:
        return {"action": "current", "pin": pin.cli_version, "model": pin.model}

    work = Path(scratch) if scratch else repo / "data" / "cache" / "grok_pin_bump"
    side = work / "candidate"
    try:
        candidate_bin = install_side_cli(
            candidate["cli_version"], side, grok_bin=grok_bin, runner=run
        )
        disposition, summary = run_smoke(
            grok_bin=str(candidate_bin),
            model=candidate["model"],
            reasoning_effort=candidate["reasoning_effort"],
            scratch=work / "smoke",
            runner=run,
        )
        new_pin = grok_pin.bump_pin(
            pin,
            model=candidate["model"],
            cli_version=candidate["cli_version"],
            reasoning_effort=candidate["reasoning_effort"],
        )
        branch = commit_pin_bump(
            repo,
            pin.path,
            new_pin,
            reason=candidate["reason"],
            smoke_summary=summary,
            runner=run,
        )
    except (GrokPinBumpError, ir_pr_description.IrDescriptionError, grok_pin.GrokPinError) as exc:
        message = f"grok pin bump failed: {exc}"
        if alerter:
            alerter(message)
        print(message, file=sys.stderr)
        return {"action": "failed", "error": str(exc), "candidate": candidate}

    return {
        "action": "committed",
        "branch": branch,
        "candidate": candidate,
        "disposition": disposition,
        "summary": summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check for a newer Grok CLI or default model; commit a pin bump."
    )
    parser.add_argument("--repo", default=".", help="clone that holds config/grok_pin.json")
    parser.add_argument("--grok-bin", default=os.environ.get("GROK_BIN") or "grok")
    parser.add_argument("--scratch", default="")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        result = run_bump(
            repo,
            grok_bin=args.grok_bin,
            scratch=Path(args.scratch) if args.scratch else None,
        )
    except (GrokPinBumpError, grok_pin.GrokPinError) as exc:
        print(f"grok pin bump failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("action") != "failed" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
