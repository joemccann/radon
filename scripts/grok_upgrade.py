"""Daily Grok CLI/model upgrader. Smoke, then auto-promote. No PR.

Installed enabled. Installs the latest stable CLI into a candidate
location, smokes it, and on pass switches the live symlink and writes
last-known-good. On fail, stay on LKG and alert. Never updates mid-incident:
``--no-auto-update`` stays on incident runs; this timer is the only upgrader
and it takes the shared runtime lock before swapping the binary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import grok_runtime
import ir_pr_description
from grok_page_responder import parse_grok_result

Runner = Callable[..., object]
SERVICE_NAME = "grok-upgrade"
SMOKE_PROMPT = """Dry-run smoke for a Grok track-latest candidate. Do not edit files.
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
RESULT: stand_down | grok upgrade smoke returned a structured IR summary
"""


class GrokUpgradeError(RuntimeError):
    """Upgrade job cannot decide, smoke, or promote."""


def _default_runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=kwargs.get("cwd"),
        env=kwargs.get("env") or grok_runtime.grok_child_env(),
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
                payload.get("currentVersion")
                or payload.get("current")
                or payload.get("installed")
                or payload.get("version")
            )
            stable = payload.get("stable")
            if isinstance(stable, dict):
                stable = stable.get("version")
            latest = (
                payload.get("latestVersion")
                or payload.get("latest")
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
            raise GrokUpgradeError(f"not a dotted version: {value!r}")
        return tuple(int(bit) for bit in bits)

    l, r = parts(left), parts(right)
    return (l > r) - (l < r)


def decide_upgrade(
    *,
    lkg: grok_runtime.LkgState | None,
    update_check: dict,
    models_stdout: str,
) -> dict | None:
    """Return a candidate, or None when live LKG already matches latest."""
    default, available = grok_runtime.parse_models_listing(models_stdout)
    latest_cli = (update_check.get("latest") or "").strip()
    if not latest_cli and not default:
        return None
    newer_cli = False
    if latest_cli and lkg is not None:
        newer_cli = _cmp_version(latest_cli, lkg.cli_version) > 0
    elif latest_cli:
        newer_cli = True
    newer_model = bool(default) and default in available and (
        lkg is None or default != lkg.model
    )
    if not newer_cli and not newer_model:
        return None
    return {
        "model": default if default else (lkg.model if lkg else ""),
        "cli_version": latest_cli or (lkg.cli_version if lkg else ""),
        "reasoning_effort": (
            lkg.reasoning_effort if lkg else grok_runtime.reasoning_effort()
        ),
        "reason": (
            (f"CLI {(lkg.cli_version if lkg else '?')} -> {latest_cli}" if newer_cli else "")
            + ("; " if newer_cli and newer_model else "")
            + (
                f"default model {(lkg.model if lkg else '?')} -> {default}"
                if newer_model
                else ""
            )
        ),
    }


def install_candidate_cli(
    dest: Path,
    *,
    grok_bin: str,
    runner: Runner,
    version: str | None = None,
) -> Path:
    # REL-292 / R-711: HOME does not isolate a self-updater's executable.
    # Execute a private copy so even a failed update cannot mutate the live CLI.
    dest.mkdir(parents=True, exist_ok=True)
    installed = dest / "bin" / "grok"
    installed.parent.mkdir(parents=True, exist_ok=True)
    source = Path(shutil.which(grok_bin) or grok_bin).resolve(strict=True)
    shutil.copy2(source, installed)
    env = grok_runtime.grok_child_env({"GROK_HOME": str(dest), "HOME": str(dest)})
    # `grok update` has no --no-auto-update (grok 1.0.3 rejects it).
    # That flag stays on the smoke invocation only.
    argv = [str(installed), "update"]
    if version:
        argv.extend(["--version", version])
    proc = runner(argv, env=env, cwd=str(dest), timeout=180)
    if getattr(proc, "returncode", 1) != 0:
        raise GrokUpgradeError(
            f"candidate grok update failed: "
            f"{(getattr(proc, 'stderr', '') or '')[:300]}"
        )
    probe = runner([str(installed), "--version"], env=env, cwd=str(dest), timeout=30)
    if getattr(probe, "returncode", 1) != 0:
        raise GrokUpgradeError("candidate version probe failed")
    actual = grok_runtime.parse_cli_version(getattr(probe, "stdout", "") or "")
    if version and actual != version:
        raise GrokUpgradeError(f"candidate version {actual} does not match requested {version}")
    return installed


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
        raise GrokUpgradeError(
            f"smoke grok exit {getattr(proc, 'returncode', 1)}: "
            f"{(getattr(proc, 'stderr', '') or '')[:300]}"
        )
    stdout = getattr(proc, "stdout", "") or ""
    disposition, summary = parse_grok_result(stdout)
    if disposition not in {"stand_down", "ops_only", "code_fix"}:
        raise GrokUpgradeError(f"smoke RESULT was {disposition}: {summary}")
    ir_pr_description.validate_ir_description(stdout, branch="fix/grok-upgrade-smoke")
    return disposition, summary


def promote_live_symlink(
    live_bin: Path,
    candidate_bin: Path,
    *,
    alias_bin: Path | None = None,
    candidate_root: Path | None = None,
) -> Path:
    """REL-292: prepare links before atomically replacing any live pathname.

    Touches only ``live_bin`` and, when given, ``alias_bin``. It never derives
    a path from HOME: on 2026-09-29 a pytest run inside the responder clone
    repointed the operator's real ``~/.grok/bin/grok`` at a pytest tmp
    candidate because this function linked ``Path.home()/.grok/bin/grok``
    unconditionally. With ``candidate_root`` the resolved candidate must lie
    under it, so a link can only ever point into the upgrader's own scratch.
    """
    target = candidate_bin.resolve(strict=True)
    if target == live_bin.absolute():
        raise GrokUpgradeError("candidate must be separate from the live executable")
    if candidate_root is not None and not target.is_relative_to(
        Path(candidate_root).resolve()
    ):
        raise GrokUpgradeError(
            f"candidate {target} is outside the upgrade scratch {candidate_root}"
        )
    live_bin.parent.mkdir(parents=True, exist_ok=True)
    # The secondary entry always follows the canonical live path. Preparing
    # both links first leaves the old CLI reachable if symlink creation fails.
    links = [(live_bin, target)]
    if alias_bin is not None and alias_bin.absolute() != live_bin.absolute():
        if not alias_bin.parent.is_dir():
            raise GrokUpgradeError(f"alias directory missing: {alias_bin.parent}")
        links.insert(0, (alias_bin, live_bin.absolute()))
    prepared = []
    try:
        for destination, value in links:
            fd, name = tempfile.mkstemp(prefix=".grok-link-", dir=destination.parent)
            os.close(fd)
            temporary = Path(name)
            temporary.unlink()
            prepared.append((temporary, destination))
            temporary.symlink_to(value)
        for temporary, destination in prepared:
            temporary.replace(destination)
    finally:
        for temporary, _ in prepared:
            temporary.unlink(missing_ok=True)
    return live_bin


def seed_lkg_if_missing(
    *,
    grok_bin: str,
    lkg_path: Path,
    runner: Runner,
) -> grok_runtime.LkgState | None:
    existing = grok_runtime.load_lkg(lkg_path)
    if existing is not None:
        return existing
    version, default, _available = grok_runtime.probe_cli(grok_bin, runner=runner)
    if not default:
        raise GrokUpgradeError("cannot seed LKG: grok models has no default")
    state = grok_runtime.LkgState(
        cli_version=version,
        binary_path=str(Path(grok_bin)),
        model=default,
        reasoning_effort=grok_runtime.reasoning_effort(),
        promoted_at=grok_runtime.now_iso(),
        smoke_result="seed: live CLI/model recorded before first upgrade smoke",
    )
    grok_runtime.write_lkg(lkg_path, state)
    return state


def _record_health(state: str, detail: str | None = None) -> None:
    try:
        from db.hrana_http import write_service_health_http
    except ImportError:
        return
    # REL-293 / R-712: the transport accepts a structured error, not last_error.
    error = {"message": detail} if detail else None
    try:
        write_service_health_http(SERVICE_NAME, state, error=error)
    except Exception as exc:  # noqa: BLE001 — telemetry must not fail the job
        print(f"grok upgrade heartbeat non-fatal: {exc}", file=sys.stderr)


def _alert(message: str, alerter: Optional[Callable[[str], None]]) -> None:
    print(message, file=sys.stderr)
    if alerter:
        alerter(message)
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
            title="radon grok: upgrade stayed on last-known-good",
            message=message[:900],
            severity=None,
        )
    )


def run_upgrade(
    *,
    grok_bin: str,
    live_bin: Path | None = None,
    alias_bin: Path | None = None,
    lkg_path: Path | None = None,
    lock_path: Path | None = None,
    scratch: Path | None = None,
    runner: Optional[Runner] = None,
    alerter: Optional[Callable[[str], None]] = None,
) -> dict:
    run = runner or _default_runner
    lkg_file = Path(lkg_path) if lkg_path else grok_runtime.DEFAULT_LKG_PATH
    lock_file = Path(lock_path) if lock_path else grok_runtime.DEFAULT_LOCK_PATH
    live = Path(live_bin) if live_bin else Path(grok_bin)
    lkg = grok_runtime.load_lkg(lkg_file)
    check_proc = run([grok_bin, "update", "--check", "--json"])
    if getattr(check_proc, "returncode", 1) != 0:
        raise GrokUpgradeError("grok update --check failed")
    models_proc = run([grok_bin, "models"])
    if getattr(models_proc, "returncode", 1) != 0:
        raise GrokUpgradeError("grok models failed")
    candidate = decide_upgrade(
        lkg=lkg,
        update_check=parse_update_check(getattr(check_proc, "stdout", "") or ""),
        models_stdout=getattr(models_proc, "stdout", "") or "",
    )
    if candidate is None:
        _record_health("ok")
        return {
            "action": "current",
            "cli_version": lkg.cli_version if lkg else "",
            "model": lkg.model if lkg else "",
        }

    work = Path(scratch) if scratch else Path("data/cache/grok_upgrade")
    work.mkdir(parents=True, exist_ok=True)
    # Never reuse the directory an earlier promotion may still execute from.
    side = Path(tempfile.mkdtemp(prefix="candidate-", dir=work))
    try:
        candidate_bin = install_candidate_cli(
            side,
            grok_bin=grok_bin,
            runner=run,
            version=candidate["cli_version"] or None,
        )
        disposition, summary = run_smoke(
            grok_bin=str(candidate_bin),
            model=candidate["model"],
            reasoning_effort=candidate["reasoning_effort"],
            scratch=work / "smoke",
            runner=run,
        )
        try:
            with grok_runtime.exclusive_lock(lock_file, blocking=False):
                promote_live_symlink(
                    live,
                    candidate_bin,
                    alias_bin=Path(alias_bin) if alias_bin else None,
                    candidate_root=work,
                )
                state = grok_runtime.LkgState(
                    cli_version=candidate["cli_version"],
                    binary_path=str(candidate_bin.resolve()),
                    model=candidate["model"],
                    reasoning_effort=candidate["reasoning_effort"],
                    promoted_at=grok_runtime.now_iso(),
                    smoke_result=f"{disposition}: {summary}",
                )
                grok_runtime.write_lkg(lkg_file, state)
        except grok_runtime.GrokRuntimeError as exc:
            if "lock busy" not in str(exc):
                raise
            _record_health("ok", "promotion deferred; incident lock held")
            return {
                "action": "locked",
                "candidate": candidate,
                "error": str(exc),
            }
    except (GrokUpgradeError, ir_pr_description.IrDescriptionError, grok_runtime.GrokRuntimeError) as exc:
        message = f"grok upgrade failed; staying on last-known-good: {exc}"
        _alert(message, alerter)
        _record_health("error", str(exc)[:300])
        return {"action": "failed", "error": str(exc), "candidate": candidate}
    finally:
        if not live.resolve().is_relative_to(side):
            shutil.rmtree(side)

    _record_health("ok")
    return {
        "action": "promoted",
        "candidate": candidate,
        "disposition": disposition,
        "summary": summary,
        "lkg": str(lkg_file),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install latest stable Grok, smoke it, auto-promote on pass."
    )
    parser.add_argument("--grok-bin", default=os.environ.get("GROK_BIN") or "grok")
    parser.add_argument("--live-bin", default="")
    parser.add_argument(
        "--alias-bin",
        default="",
        help="Secondary entry (e.g. ~/.grok/bin/grok) relinked to --live-bin on "
        "promote. Never derived from HOME; omitted means only --live-bin moves.",
    )
    parser.add_argument("--lkg", default="")
    parser.add_argument("--lock", default="")
    parser.add_argument("--scratch", default="")
    parser.add_argument(
        "--seed-if-missing",
        action="store_true",
        help="Write LKG from the live CLI/model when the state file is absent.",
    )
    args = parser.parse_args(argv)
    lkg_path = Path(args.lkg) if args.lkg else grok_runtime.DEFAULT_LKG_PATH
    try:
        if args.seed_if_missing:
            state = seed_lkg_if_missing(
                grok_bin=args.grok_bin,
                lkg_path=lkg_path,
                runner=_default_runner,
            )
            print(json.dumps({"action": "seeded", "lkg": state.as_json() if state else None}))
            return 0
        result = run_upgrade(
            grok_bin=args.grok_bin,
            live_bin=Path(args.live_bin) if args.live_bin else None,
            alias_bin=Path(args.alias_bin) if args.alias_bin else None,
            lkg_path=lkg_path,
            lock_path=Path(args.lock) if args.lock else None,
            scratch=Path(args.scratch) if args.scratch else None,
        )
    except (GrokUpgradeError, grok_runtime.GrokRuntimeError) as exc:
        print(f"grok upgrade failed: {exc}", file=sys.stderr)
        _record_health("error", str(exc)[:300])
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("action") != "failed" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
