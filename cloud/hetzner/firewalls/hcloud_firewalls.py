#!/usr/bin/env python3
"""Render and apply the Radon Hetzner Cloud Firewalls (Ops Plane step 2E).

OPERATOR-RUN ONLY; deploy never calls this. Dry run by default:

    python3 cloud/hetzner/firewalls/hcloud_firewalls.py \\
        --firewall fw-radon-broker --server radon-broker --recovery-ip 203.0.113.7

prints the rendered rules and the hcloud commands. --apply runs them with the
operator's own `hcloud` context: create the firewall if missing, replace its
rules, attach it to the server. Rule files are <name>.json beside this file;
`__OPERATOR_RECOVERY_IP__/32` is replaced with --recovery-ip
(or RADON_HCLOUD_RECOVERY_IP), a single public IPv4.

Hetzner Cloud Firewalls filter the public interface only; radon-private
traffic is never seen here. Runbook: docs/operations.md
"Hetzner Cloud Firewalls". Pinned by cloud/tests/test_hetzner_firewalls.py.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLACEHOLDER = "__OPERATOR_RECOVERY_IP__/32"
FIREWALLS = ("fw-radon-app", "fw-radon-broker", "fw-radon-ops")


def _recovery_cidr(value: str | None) -> str:
    try:
        address = ipaddress.IPv4Address((value or "").strip())
    except ValueError:
        raise ValueError(f"recovery address must be one public IPv4: {value!r}") from None
    # A Hetzner firewall sees public source addresses only: refuse private,
    # tailnet (CGNAT), loopback and other non-routable values.
    if not address.is_global:
        raise ValueError(f"recovery address must be public, got {address}")
    return f"{address}/32"


def render(name: str, recovery_ip: str | None) -> list[dict]:
    if name not in FIREWALLS:
        raise ValueError(f"unknown firewall {name!r}; expected one of {FIREWALLS}")
    rules = json.loads((HERE / f"{name}.json").read_text(encoding="utf-8"))
    if any(PLACEHOLDER in rule["source_ips"] for rule in rules):
        cidr = _recovery_cidr(recovery_ip)
        for rule in rules:
            rule["source_ips"] = [cidr if s == PLACEHOLDER else s for s in rule["source_ips"]]
    return rules


def commands(name: str, server: str, rules_file: str) -> list[list[str]]:
    return [
        ["hcloud", "firewall", "replace-rules", name, "--rules-file", rules_file],
        ["hcloud", "firewall", "apply-to-resource", name, "--type", "server", "--server", server],
    ]


def _exists(name: str) -> bool:
    res = subprocess.run(
        ["hcloud", "firewall", "describe", name], capture_output=True, text=True
    )
    return res.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--firewall", required=True, choices=FIREWALLS)
    parser.add_argument("--server", required=True, help="hcloud server name to attach to")
    parser.add_argument("--recovery-ip", default=os.environ.get("RADON_HCLOUD_RECOVERY_IP"))
    parser.add_argument("--apply", action="store_true", help="run the hcloud commands")
    args = parser.parse_args(argv)

    try:
        rules = render(args.firewall, args.recovery_ip)
    except ValueError as exc:
        print(f"hcloud_firewalls: {exc}", file=sys.stderr)
        return 2

    with tempfile.NamedTemporaryFile(
        "w", suffix=f"-{args.firewall}.json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(rules, handle, indent=2)
        rules_file = handle.name
    planned = commands(args.firewall, args.server, rules_file)

    if not args.apply:
        print(f"# dry run: {args.firewall} -> server {args.server}; re-run with --apply")
        print(json.dumps(rules, indent=2))
        print(f"hcloud firewall create --name {args.firewall}   # only if missing")
        for cmd in planned:
            print(" ".join(cmd))
        return 0

    if not _exists(args.firewall):
        planned.insert(0, ["hcloud", "firewall", "create", "--name", args.firewall])
    for cmd in planned:
        print("+ " + " ".join(cmd), flush=True)
        if subprocess.run(cmd).returncode != 0:
            print(f"hcloud_firewalls: failed: {' '.join(cmd)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
