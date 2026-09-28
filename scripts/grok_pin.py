"""Pinned Grok model + CLI. The responder never uses an unpinned default."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

Runner = Callable[..., object]

_SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_PIN_PATH = _SCRIPTS_DIR.parent / "config" / "grok_pin.json"
REASONING_EFFORTS = frozenset({"low", "medium", "high"})
_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")
_DEFAULT_MODEL_RE = re.compile(r"(?im)^default model:\s*(\S+)")
_MODEL_LINE_RE = re.compile(r"(?im)^[ \t]*([a-z0-9][a-z0-9._-]{2,})")


class GrokPinError(ValueError):
    """Pin file or live CLI probe is unusable."""


@dataclass(frozen=True)
class GrokPin:
    model: str
    reasoning_effort: str
    cli_version: str
    lkg_model: str
    lkg_reasoning_effort: str
    lkg_cli_version: str
    path: Path


@dataclass(frozen=True)
class ResolvedRuntime:
    model: str
    reasoning_effort: str
    cli_version: str
    used_fallback: bool
    warning: Optional[str]
    refused: bool


def load_pin(path: Path | None = None) -> GrokPin:
    pin_path = Path(path) if path else DEFAULT_PIN_PATH
    try:
        raw = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GrokPinError(f"cannot read grok pin {pin_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise GrokPinError("grok pin must be a JSON object")
    lkg = raw.get("last_known_good") or {}
    if not isinstance(lkg, dict):
        raise GrokPinError("last_known_good must be an object")

    def _field(src: dict, key: str, fallback: str | None = None) -> str:
        value = src.get(key, fallback)
        if not isinstance(value, str) or not value.strip():
            raise GrokPinError(f"grok pin missing {key}")
        return value.strip()

    model = _field(raw, "model")
    effort = _field(raw, "reasoning_effort").lower()
    cli = _field(raw, "cli_version")
    if effort not in REASONING_EFFORTS:
        raise GrokPinError(f"reasoning_effort must be one of {sorted(REASONING_EFFORTS)}")
    lkg_model = _field(lkg, "model", model)
    lkg_effort = _field(lkg, "reasoning_effort", effort).lower()
    if lkg_effort not in REASONING_EFFORTS:
        raise GrokPinError("last_known_good.reasoning_effort is invalid")
    lkg_cli = _field(lkg, "cli_version", cli)
    return GrokPin(
        model=model,
        reasoning_effort=effort,
        cli_version=cli,
        lkg_model=lkg_model,
        lkg_reasoning_effort=lkg_effort,
        lkg_cli_version=lkg_cli,
        path=pin_path,
    )


def parse_cli_version(stdout: str) -> str:
    match = _VERSION_RE.search(stdout or "")
    if not match:
        raise GrokPinError("grok --version did not report x.y.z")
    return match.group(1)


def parse_models_listing(stdout: str) -> tuple[str | None, set[str]]:
    text = stdout or ""
    default = None
    match = _DEFAULT_MODEL_RE.search(text)
    if match:
        default = match.group(1).strip()
    available: set[str] = set()
    for line in text.splitlines():
        row = line.strip()
        if not row or row.lower().startswith("default model"):
            continue
        token = row.split()[0].strip("(),")
        if _MODEL_LINE_RE.fullmatch(token):
            available.add(token)
    if default:
        available.add(default)
    return default, available


def _run(runner: Runner, argv: list[str]) -> str:
    proc = runner(argv)
    if getattr(proc, "returncode", 1) != 0:
        raise GrokPinError(
            f"{argv[0]} failed: {(getattr(proc, 'stderr', '') or '')[:200]}"
        )
    return getattr(proc, "stdout", "") or ""


def probe_cli(
    grok_bin: str,
    *,
    runner: Runner,
) -> tuple[str, str | None, set[str]]:
    version = parse_cli_version(_run(runner, [grok_bin, "--version"]))
    default, available = parse_models_listing(_run(runner, [grok_bin, "models"]))
    return version, default, available


def resolve_runtime(
    pin: GrokPin,
    *,
    grok_bin: str,
    runner: Runner,
) -> ResolvedRuntime:
    """Pick pin or last-known-good. Never drop ``-m`` for the CLI default."""
    try:
        installed, _default, available = probe_cli(grok_bin, runner=runner)
    except GrokPinError as exc:
        return ResolvedRuntime(
            model=pin.lkg_model,
            reasoning_effort=pin.lkg_reasoning_effort,
            cli_version=pin.lkg_cli_version,
            used_fallback=True,
            warning=f"grok probe failed ({exc}); standing down rather than using an unpinned default",
            refused=True,
        )

    pin_ok = installed == pin.cli_version and pin.model in available
    if pin_ok:
        return ResolvedRuntime(
            model=pin.model,
            reasoning_effort=pin.reasoning_effort,
            cli_version=pin.cli_version,
            used_fallback=False,
            warning=None,
            refused=False,
        )

    reasons = []
    if installed != pin.cli_version:
        reasons.append(f"CLI {installed} != pin {pin.cli_version}")
    if pin.model not in available:
        reasons.append(f"pinned model {pin.model} unavailable")
    lkg_model_ok = pin.lkg_model in available
    if not lkg_model_ok:
        return ResolvedRuntime(
            model=pin.lkg_model,
            reasoning_effort=pin.lkg_reasoning_effort,
            cli_version=pin.lkg_cli_version,
            used_fallback=True,
            warning=(
                f"grok pin mismatch ({'; '.join(reasons)}) and last-known-good "
                f"model {pin.lkg_model} is also unavailable; refusing unpinned default"
            ),
            refused=True,
        )
    return ResolvedRuntime(
        model=pin.lkg_model,
        reasoning_effort=pin.lkg_reasoning_effort,
        cli_version=pin.lkg_cli_version,
        used_fallback=True,
        warning=(
            f"grok pin mismatch ({'; '.join(reasons)}); falling back to "
            f"last-known-good {pin.lkg_model} / CLI {pin.lkg_cli_version}"
        ),
        refused=False,
    )


def write_pin(path: Path, pin: GrokPin) -> None:
    payload = {
        "model": pin.model,
        "reasoning_effort": pin.reasoning_effort,
        "cli_version": pin.cli_version,
        "last_known_good": {
            "model": pin.lkg_model,
            "reasoning_effort": pin.lkg_reasoning_effort,
            "cli_version": pin.lkg_cli_version,
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def bump_pin(
    current: GrokPin,
    *,
    model: str | None = None,
    cli_version: str | None = None,
    reasoning_effort: str | None = None,
) -> GrokPin:
    """Promote the live pin; today's pin becomes last-known-good."""
    effort = (reasoning_effort or current.reasoning_effort).lower()
    if effort not in REASONING_EFFORTS:
        raise GrokPinError("reasoning_effort is invalid")
    return GrokPin(
        model=model or current.model,
        reasoning_effort=effort,
        cli_version=cli_version or current.cli_version,
        lkg_model=current.model,
        lkg_reasoning_effort=current.reasoning_effort,
        lkg_cli_version=current.cli_version,
        path=current.path,
    )
