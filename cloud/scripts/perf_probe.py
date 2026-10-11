#!/usr/bin/env python3
"""Read-only production performance snapshot for the nightly performance loop.

The Mac runner's `performance` loop reaches this script over ssh with a key
whose authorized_keys line is

    restrict,command="/usr/bin/python3 -I /home/radon/radon/cloud/scripts/perf_probe.py" ssh-ed25519 ...

so the key runs this file and nothing else: `SSH_ORIGINAL_COMMAND` is
ignored, there is no shell, pty or forwarding. It runs as `radon`, never root,
and only reads: /proc, `systemctl show`, `journalctl` counts, loopback GETs of
the side-effect-free health endpoints and TLS handshakes (no request bytes)
to the upstream hosts. It never restarts, writes, attaches to a process, or
reads a secret file. Every section is bounded; a section that fails reports
its error and the rest still run. Stdlib only, 3.9-clean.

Output: one JSON object on stdout.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import ssl
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCHEMA = 1
SAMPLE_SECS = 5.0
HTTP_SAMPLES = 10
TLS_SAMPLES = 5
CMD_TIMEOUT = 20

#: Loopback GETs only, all side-effect-free (scripts/api/server.py health_lite
#: never touches the IB pool). Never /health: it can drive a reconnect.
HTTP_TARGETS = (
    ("api_health_lite", "http://127.0.0.1:8321/health/lite"),
    ("health_daemon", "http://127.0.0.1:8330/healthz"),
    ("nextjs_root", "http://127.0.0.1:3000/"),
)
#: Upstreams the stack depends on. Handshake only: no request is sent.
TLS_TARGETS = (
    "radon-joemccann.aws-us-west-2.turso.io",
    "api.unusualwhales.com",
    "radon.run",
    "media.radon.run",
)
DISK_PATHS = ("/", "/home/radon", "/var/lib/docker", "/var/log")
JOURNAL_SINCE = "-24h"
JOURNAL_TOP = 15
UNIT_PROPS = (
    "ActiveState", "SubState", "NRestarts", "CPUUsageNSec", "MemoryCurrent",
    "MemoryPeak", "TasksCurrent", "IOReadBytes", "IOWriteBytes",
    "ActiveEnterTimestamp", "ExecMainStartTimestamp",
)


def _run(argv, timeout=CMD_TIMEOUT):
    out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"{argv[0]} exit {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 2)


def host(proc="/proc"):
    load = _read(f"{proc}/loadavg").split()
    mem = {}
    for line in _read(f"{proc}/meminfo").splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Dirty", "Cached"):
            mem[key + "_kb"] = int(rest.split()[0])
    pressure = {}
    for res in ("cpu", "io", "memory"):
        try:
            for line in _read(f"{proc}/pressure/{res}").splitlines():
                kind, *fields = line.split()
                pressure[f"{res}_{kind}"] = {k: float(v) for k, v in (f.split("=") for f in fields) if k != "total"}
        except OSError:
            pass
    vm = dict(line.split() for line in _read(f"{proc}/vmstat").splitlines())
    return {
        "cpus": os.cpu_count(),
        "loadavg": [float(x) for x in load[:3]],
        "uptime_s": float(_read(f"{proc}/uptime").split()[0]),
        "mem": mem,
        "psi": pressure,
        "swap_in_pages": int(vm.get("pswpin", 0)),
        "swap_out_pages": int(vm.get("pswpout", 0)),
        "oom_kills": int(vm.get("oom_kill", 0)),
    }


def _diskstats(proc):
    stats = {}
    for line in _read(f"{proc}/diskstats").splitlines():
        f = line.split()
        if len(f) < 14 or re.match(r"(loop|ram|dm-)", f[2]):
            continue
        # reads, ms reading, writes, ms writing, ms doing io
        stats[f[2]] = (int(f[3]), int(f[6]), int(f[7]), int(f[10]), int(f[12]))
    return stats


def _netdev(proc):
    stats = {}
    for line in _read(f"{proc}/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if name.strip() != "lo":
            stats[name.strip()] = (int(f[0]), int(f[8]), int(f[2]) + int(f[3]), int(f[10]) + int(f[11]))
    return stats


def _tcp(proc):
    lines = [line.split() for line in _read(f"{proc}/net/snmp").splitlines() if line.startswith("Tcp:")]
    row = dict(zip(lines[0][1:], (int(x) for x in lines[1][1:])))
    return row.get("OutSegs", 0), row.get("RetransSegs", 0)


def io_and_net(proc="/proc", secs=SAMPLE_SECS, sleep=time.sleep):
    d0, n0, t0 = _diskstats(proc), _netdev(proc), _tcp(proc)
    sleep(secs)
    d1, n1, t1 = _diskstats(proc), _netdev(proc), _tcp(proc)
    disks = {}
    for dev, b in d1.items():
        a = d0.get(dev)
        if not a:
            continue
        ops = (b[0] - a[0]) + (b[2] - a[2])
        disks[dev] = {
            "iops": round(ops / secs, 1),
            "await_ms": round(((b[1] - a[1]) + (b[3] - a[3])) / ops, 2) if ops else 0.0,
            "util_pct": round(100.0 * (b[4] - a[4]) / (secs * 1000), 1),
        }
    nics = {}
    for nic, b in n1.items():
        a = n0.get(nic)
        if a:
            nics[nic] = {"rx_kbps": round((b[0] - a[0]) / secs / 1024, 1),
                         "tx_kbps": round((b[1] - a[1]) / secs / 1024, 1),
                         "errs_drops": b[2] + b[3] - a[2] - a[3]}
    segs = t1[0] - t0[0]
    return {
        "window_s": secs,
        "disks": disks,
        "nics": nics,
        "tcp_retrans_pct": round(100.0 * (t1[1] - t0[1]) / segs, 3) if segs else 0.0,
        "tcp_retrans_total": t1[1],
    }


def disks(paths=DISK_PATHS):
    out = {}
    for path in paths:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        out[path] = {"used_pct": round(100.0 * usage.used / usage.total, 1), "free_gb": round(usage.free / 2**30, 1)}
    return out


def units(run=_run):
    names = [line.split()[0] for line in run(
        ["systemctl", "list-units", "radon-*", "--all", "--plain", "--no-legend", "--no-pager"]).splitlines()
        if line.strip()]
    if not names:
        return {}
    raw = run(["systemctl", "show", "--no-pager", "-p", ",".join(UNIT_PROPS), *names])
    out, blocks = {}, raw.strip().split("\n\n")
    for name, block in zip(names, blocks):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        row = {}
        for key, value in props.items():
            if value in ("", "[not set]", "18446744073709551615"):
                continue
            row[key] = int(value) if value.isdigit() else value
        out[name] = row
    return out


_NOISE = re.compile(r"(0x[0-9a-f]+|[0-9a-f]{8,}|\d+(\.\d+)?)", re.I)
# Credential-shaped text a warning line can carry; the snapshot is read by
# third-party agents, so it is collapsed before a template is kept.
_SECRETS = (
    (re.compile(r"(://)[^/\s@]+@"), r"\1#@"),
    (re.compile(r"\?[^\s\"']*"), "?#"),
    (re.compile(r"\b(bearer|basic)\s+\S+", re.I), r"\1 #"),
    (re.compile(r"\b(\w*(?:token|secret|passw(?:or)?d|api[_-]?key|auth\w*|cookie|session)\w*)([\"']?)\s*[=:]\s*"
                r"(\"[^\"]*\"|'[^']*'|\S+)", re.I), r"\1\2=#"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "#"),
)
_OPAQUE = re.compile(r"[\w+/=-]{20,}")


def _opaque(match):
    run = match.group(0)
    if any(c.isdigit() for c in run) and any(c.isalpha() for c in run):
        return "#"
    return run


def _template(msg):
    for pattern, repl in _SECRETS:
        msg = pattern.sub(repl, msg)
    msg = _OPAQUE.sub(_opaque, msg)
    return _NOISE.sub("#", msg)[:160]


def journal(run=_run):
    """Warning-and-worse counts per radon unit plus the most frequent
    templates (credential-shaped text, digits and hex collapsed, 160 chars).
    Counts, not log lines."""
    raw = run(["journalctl", "--no-pager", "-q", "-o", "json", "-p", "warning",
               "--since", JOURNAL_SINCE, "-u", "radon-*"], timeout=60)
    per_unit, templates = {}, {}
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        unit = entry.get("_SYSTEMD_UNIT") or entry.get("UNIT") or "?"
        msg = entry.get("MESSAGE")
        if not isinstance(msg, str):
            continue
        per_unit[unit] = per_unit.get(unit, 0) + 1
        key = (unit, _template(msg))
        templates[key] = templates.get(key, 0) + 1
    top = sorted(templates.items(), key=lambda kv: -kv[1])[:JOURNAL_TOP]
    return {"since": JOURNAL_SINCE, "per_unit": per_unit,
            "top": [{"unit": u, "count": c, "template": t} for (u, t), c in top]}


def http_latency(targets=HTTP_TARGETS, samples=HTTP_SAMPLES, opener=urllib.request.urlopen):
    out = {}
    for name, url in targets:
        times, status, size, err = [], None, None, None
        for _ in range(samples):
            start = time.perf_counter()
            try:
                with opener(urllib.request.Request(url, method="GET"), timeout=10) as resp:
                    size = len(resp.read())
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                err = type(exc).__name__
                break
            times.append((time.perf_counter() - start) * 1000)
        out[name] = {"url": url, "status": status, "bytes": size, "n": len(times),
                     "p50_ms": _pct(times, 0.5), "p95_ms": _pct(times, 0.95),
                     "max_ms": round(max(times), 2) if times else None, "error": err}
    return out


def tls_handshakes(hosts=TLS_TARGETS, samples=TLS_SAMPLES):
    ctx = ssl.create_default_context()
    out = {}
    for hostname in hosts:
        dns, conn, tls, err = [], [], [], None
        for _ in range(samples):
            try:
                t0 = time.perf_counter()
                addr = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)[0][4]
                t1 = time.perf_counter()
                with socket.create_connection(addr[:2], timeout=10) as sock:
                    t2 = time.perf_counter()
                    with ctx.wrap_socket(sock, server_hostname=hostname):
                        t3 = time.perf_counter()
            except Exception as exc:  # noqa: BLE001
                err = type(exc).__name__
                break
            dns.append((t1 - t0) * 1000)
            conn.append((t2 - t1) * 1000)
            tls.append((t3 - t2) * 1000)
        out[hostname] = {"n": len(tls), "dns_p50_ms": _pct(dns, 0.5), "tcp_p50_ms": _pct(conn, 0.5),
                         "tls_p50_ms": _pct(tls, 0.5), "tls_max_ms": round(max(tls), 2) if tls else None,
                         "error": err}
    return out


SECTIONS = (
    ("host", host),
    ("io_net", io_and_net),
    ("disk_usage", disks),
    ("units", units),
    ("journal", journal),
    ("http", http_latency),
    ("tls", tls_handshakes),
)


def collect(sections=None):
    report = {"schema": SCHEMA, "host": socket.gethostname(),
              "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "errors": {}}
    for name, fn in sections or SECTIONS:
        start = time.perf_counter()
        try:
            report[name] = fn()
        except Exception as exc:  # noqa: BLE001 - one section never sinks the rest
            report["errors"][name] = f"{type(exc).__name__}: {str(exc)[:200]}"
        report.setdefault("section_ms", {})[name] = round((time.perf_counter() - start) * 1000, 1)
    return report


def main():
    json.dump(collect(), sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
