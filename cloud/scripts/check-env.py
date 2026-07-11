#!/usr/bin/env python3
"""Validate the production dotenv contract without expanding or printing values."""

from __future__ import annotations

import argparse
import re
import stat
from pathlib import Path


KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
DOLLAR_REFERENCE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")
GATEWAY_PORT_BY_MODE = {"live": "4001", "paper": "4002"}
PRODUCTION_INVARIANTS = {
    "IB_GATEWAY_MODE": "cloud",
    "IB_GATEWAY_HOST": "127.0.0.1",
    "RADON_MODE": "cloud",
    "NODE_ENV": "production",
}


def required_keys(path: Path) -> list[str]:
    keys = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    invalid = [key for key in keys if KEY_PATTERN.fullmatch(key) is None]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if invalid or duplicates:
        details = []
        if invalid:
            details.append("invalid keys: " + " ".join(invalid))
        if duplicates:
            details.append("duplicate keys: " + " ".join(duplicates))
        raise ValueError("; ".join(details))
    return keys


def dotenv_assignments(path: Path) -> tuple[dict[str, str], list[int]]:
    assignments: dict[str, str] = {}
    invalid_lines: list[int] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, raw_value = stripped.partition("=")
        if not separator or KEY_PATTERN.fullmatch(key) is None:
            invalid_lines.append(number)
            continue
        assignments[key] = raw_value.strip()
    return assignments, invalid_lines


def literal_value(raw_value: str) -> str:
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
        return raw_value[1:-1]
    return raw_value


def gateway_contract_errors(assignments: dict[str, str]) -> list[str]:
    raw_mode = assignments.get("TRADING_MODE")
    raw_port = assignments.get("IB_GATEWAY_PORT")
    missing = [
        key
        for key, raw_value in (("TRADING_MODE", raw_mode), ("IB_GATEWAY_PORT", raw_port))
        if raw_value is None or literal_value(raw_value) == ""
    ]
    if missing:
        return ["missing gateway safety vars: " + " ".join(missing)]

    trading_mode = literal_value(raw_mode)
    gateway_port = literal_value(raw_port)
    expected_port = GATEWAY_PORT_BY_MODE.get(trading_mode)
    if expected_port is None:
        return ["TRADING_MODE must be live or paper; IB_GATEWAY_PORT cannot be validated"]
    if gateway_port != expected_port:
        return [
            "IB_GATEWAY_PORT does not match TRADING_MODE "
            "(live requires 4001; paper requires 4002)"
        ]
    return []


def production_invariant_errors(assignments: dict[str, str]) -> list[str]:
    return [
        f"{key} must match the production safety invariant"
        for key, expected in PRODUCTION_INVARIANTS.items()
        if key not in assignments or literal_value(assignments[key]) != expected
    ]


def validate(env_file: Path, contract: Path, *, skip_required: bool) -> list[str]:
    errors: list[str] = []
    if not env_file.is_file():
        return [f"env file not found: {env_file}"]
    if not contract.is_file():
        return [f"required-key contract not found: {contract}"]

    mode = stat.S_IMODE(env_file.stat().st_mode)
    if mode != 0o600:
        errors.append(f"env file permissions must be 0600 (found {mode:04o})")

    try:
        keys = required_keys(contract)
    except ValueError as exc:
        return [f"invalid required-key contract: {exc}"]
    assignments, invalid_lines = dotenv_assignments(env_file)
    if invalid_lines:
        errors.append("invalid dotenv assignments at lines: " + " ".join(map(str, invalid_lines)))

    if not skip_required:
        missing = [key for key in keys if key not in assignments]
        empty = [
            key
            for key in keys
            if key in assignments and assignments[key] in {"", "''", '\"\"'}
        ]
        if missing:
            errors.append("missing required vars: " + " ".join(missing))
        if empty:
            errors.append("empty required vars: " + " ".join(empty))

    unsafe = sorted(
        key
        for key, raw_value in assignments.items()
        if DOLLAR_REFERENCE.search(raw_value)
        and not (raw_value.startswith("'") and raw_value.endswith("'"))
    )
    if unsafe:
        errors.append("dollar-containing values must be single-quoted: " + " ".join(unsafe))
    errors.extend(production_invariant_errors(assignments))
    errors.extend(gateway_contract_errors(assignments))
    return errors


def main() -> int:
    default_contract = Path(__file__).resolve().parents[1] / "config" / "required-env.txt"
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("contract", type=Path, nargs="?", default=default_contract)
    parser.add_argument("--skip-required", action="store_true")
    args = parser.parse_args()

    errors = validate(args.env_file, args.contract, skip_required=args.skip_required)
    if errors:
        for error in errors:
            print(f"[env-check] FAIL: {error}")
        return 1
    count = len(required_keys(args.contract))
    qualifier = "required-key checks skipped; " if args.skip_required else ""
    print(f"[env-check] ok ({qualifier}{count} contract keys)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
