#!/usr/bin/env python3
"""Render the maintained network overview; firewall labels come from policy JSON.

Run from any directory. --check refuses a missing or stale published SVG.
Relationships are curated here; their operator owner is docs/spof-host-split.md.
This renders declared policy, not a live provider inventory or an apply command.
"""
from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/radon-network-topology.svg'


def firewall_lines(root: Path, role: str) -> list[str]:
    rules = json.loads((root / f'cloud/hetzner/firewalls/fw-radon-{role}.json').read_text())
    lines = []
    for rule in rules:
        if rule['direction'] != 'in':
            continue
        sources = rule['source_ips']
        if set(sources) == {'0.0.0.0/0', '::/0'}:
            scope = 'public IPv4/IPv6'
        elif sources == ['__OPERATOR_RECOVERY_IP__/32']:
            scope = 'recovery IPv4 /32 only'
        else:
            scope = ', '.join(sources)
        lines.append(f"{rule['protocol'].upper()} {rule['port']} · {scope}")
    return lines


def render(root: Path = ROOT) -> str:
    parts = ['''<svg xmlns="http://www.w3.org/2000/svg" width="1700" height="1150" viewBox="0 0 1700 1150" role="img" aria-labelledby="title description">
<title id="title">Radon network access and isolation</title>
<desc id="description">CI and operator administration use Tailscale to reach OpenSSH. CI reaches only the app host. Public SSH on broker and demo is limited to a recovery IPv4 /32. Demo has no tailnet or broker connection. Public firewall labels are generated from checked-in policy JSON; live installation requires separate verification.</desc>
<defs>
<marker id="green" markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#05AD98"/></marker>
<marker id="amber" markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#F5A623"/></marker>
<marker id="muted" markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#94a3b8"/></marker>
</defs>
<style>
text{font-family:Inter,Arial,sans-serif;fill:#e2e8f0;font-size:18px}
.title{font-size:28px;font-weight:600}.head{font-size:20px;font-weight:600}.label{font-family:'IBM Plex Mono',monospace;font-size:15px;fill:#94a3b8}
.green{fill:#05AD98}.amber{fill:#F5A623}.small{font-size:15px}.line{stroke:#1e293b;stroke-width:1}.box{fill:#0f1519;stroke:#1e293b;stroke-width:1.5;rx:4}
</style>
<rect width="1700" height="1150" fill="#0a0f14"/>
<text x="40" y="44" class="title">Radon network access and isolation</text>
<text x="40" y="74" class="label">DECLARED FIREWALLS · PRIVATE DEPLOYMENTS · SEPARATE DEMO</text>
<rect x="480" y="112" width="1180" height="380" fill="none" stroke="#1e293b" stroke-dasharray="5 5"/>
<text x="500" y="104" class="label">HETZNER PRODUCTION · APP / BROKER PRIVATE NETWORK</text>
<rect x="480" y="620" width="1180" height="390" fill="none" stroke="#1e293b" stroke-dasharray="5 5"/>
<text x="500" y="612" class="label">DEMO · VERCEL + SEPARATE HETZNER VM · NO PRODUCTION NETWORK OR TAILNET</text>''']

    def text(x, y, value, css=''):
        parts.append(f'<text x="{x}" y="{y}" class="{css}">{escape(value)}</text>')

    def box(x, y, w, h, heading, lines):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" class="box"/>')
        text(x + 20, y + 34, heading, 'head')
        parts.append(f'<path d="M{x+20} {y+50} H{x+w-20}" class="line"/>')
        for index, value in enumerate(lines):
            text(x + 20, y + 80 + index * 29, value, 'small')

    def edge(path, label, x, y, kind='green', dashed=False):
        dash = ' stroke-dasharray="7 5"' if dashed else ''
        parts.append(f'<path d="{path}" fill="none" stroke="'+{'green':'#05AD98','amber':'#F5A623','muted':'#94a3b8'}[kind]+f'" stroke-width="2"{dash} marker-end="url(#{kind})"/>')
        if label:
            text(x, y, label, f'label {kind}')

    box(40, 140, 330, 135, 'GitHub deployment jobs', ['Ephemeral tag:ci nodes', 'SSH to app only; no API or broker'])
    box(40, 340, 330, 160, 'Operator devices', ['Laptop, Mac mini, phone', 'OpenSSH with user keys', 'Works across network changes'])
    box(40, 640, 330, 135, 'Public browsers', ['app.radon.run', 'demo.radon.run'])
    app = firewall_lines(root, 'app')
    app_ssh = any('TCP 22 ·' in value for value in app)
    box(510, 140, 420, 330, 'radon-app · 10.0.0.2', ['Declared public inbound:', *app,
        'SSH: see rule above' if app_ssh else 'Public SSH: blocked', 'Tailnet SSH: 100.112.32.16:22', 'App services; no IB Gateway'])
    box(1120, 140, 500, 330, 'radon-broker · 10.0.0.4', ['Declared public inbound:', *firewall_lines(root, 'broker'),
        'Tailnet SSH: 100.67.204.57:22', 'IB Gateway + lease/watchdog owner', 'VNC and paper API: loopback only', 'Outbound TLS to IBKR'])
    box(1120, 510, 500, 100, 'Recovery public IPv4 /32', ['Broker + demo public SSH only'])
    box(510, 680, 420, 145, 'Vercel demo frontend', ['demo.radon.run · Clerk gate', 'Sample data; separate demo database'])
    box(1120, 650, 500, 320, 'radon-demo · separate Hetzner VM', ['Declared public inbound:', *firewall_lines(root, 'demo'),
        'FastAPI + Caddy admin: container-only', 'No Tailscale; no IB Gateway or IB route'])

    edge('M370 207 H510', 'SSH / tailnet', 376, 192)
    edge('M370 410 H510', 'SSH + API', 377, 396)
    edge('M370 365 H430 V90 H1370 V140', 'OpenSSH over Tailscale to broker', 760, 82)
    edge('M930 250 H1120', 'IB API 4001', 946, 236)
    edge('M930 315 H1120', 'mTLS control 8340', 940, 300)
    edge('M1120 407 H930', 'Health 8321', 952, 392, 'muted', True)
    edge('M370 686 H440 V440 H510', 'HTTPS / WSS', 377, 555)
    edge('M370 744 H510', 'HTTPS', 389, 730)
    edge('M930 750 H1120', 'HTTPS + token', 943, 735)
    edge('M1370 510 V470', '', 0, 0, 'amber')
    edge('M1370 610 V650', '', 0, 0, 'amber')
    box(40, 875, 330, 135, 'Recovery if access fails', ['App: Hetzner web console', 'Broker/demo: update recovery /32'])
    text(40, 1055, 'Green: allowed application/admin paths    Amber: public recovery SSH    Dashed: private health probe', 'label')
    text(40, 1085, 'Firewall labels: cloud/hetzner/firewalls/*.json · Access grants: cloud/tailscale/policy.hujson · Deploy jobs: ci.yml', 'label')
    text(40, 1115, 'Declared policy is not proof of live attachment. Operator rollout, IP changes and verification: docs/operations.md', 'label')
    parts.append('</svg>\n')
    return '\n'.join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if the published SVG is missing or stale')
    parser.add_argument('--output', type=Path, default=OUTPUT, help='SVG output path')
    args = parser.parse_args(argv)
    result = render()
    if args.check:
        if not args.output.exists() or args.output.read_text() != result:
            print(f'Stale network topology: regenerate {args.output}', file=sys.stderr)
            return 1
    else:
        args.output.write_text(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
