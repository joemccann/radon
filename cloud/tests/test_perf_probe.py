"""cloud/scripts/perf_probe.py: the read-only production snapshot the nightly
performance loop pulls over a forced-command ssh key.

The contract: stdlib only, never mutates, never reads a secret file, never
hits /health (it can drive an IB reconnect), and one failing section never
sinks the rest.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
PROBE = CLOUD / "scripts" / "perf_probe.py"
sys.path.insert(0, str(PROBE.parent))
import perf_probe  # noqa: E402


@pytest.fixture
def proc(tmp_path):
    root = tmp_path / "proc"
    (root / "pressure").mkdir(parents=True)
    (root / "net").mkdir()
    (root / "loadavg").write_text("0.50 0.40 0.30 1/200 1234\n")
    (root / "uptime").write_text("1000.5 900.0\n")
    (root / "meminfo").write_text("MemTotal: 8000000 kB\nMemAvailable: 4000000 kB\nSwapTotal: 0 kB\n"
                                  "SwapFree: 0 kB\nDirty: 10 kB\nCached: 100 kB\nBuffers: 1 kB\n")
    (root / "pressure" / "cpu").write_text("some avg10=1.50 avg60=1.00 avg300=0.50 total=123\n")
    (root / "vmstat").write_text("pswpin 3\npswpout 4\noom_kill 1\n")
    (root / "diskstats").write_text(
        "   8       0 sda 100 0 0 50 200 0 0 100 0 300 0 0 0 0 0 0 0\n"
        "   7       0 loop0 1 0 0 1 1 0 0 1 0 1 0\n")
    (root / "net" / "dev").write_text(
        "Inter-|   Receive\n face |bytes\n"
        "    lo: 10 1 0 0 0 0 0 0 10 1 0 0 0 0 0 0\n"
        "  eth0: 1024 1 0 0 0 0 0 0 2048 1 0 0 0 0 0 0\n")
    (root / "net" / "snmp").write_text("Tcp: OutSegs RetransSegs\nTcp: 1000 5\n")
    return root


def test_host_reads_load_memory_pressure_and_oom(proc):
    out = perf_probe.host(str(proc))
    assert out["loadavg"] == [0.5, 0.4, 0.3]
    assert out["mem"]["MemAvailable_kb"] == 4000000
    assert "Buffers_kb" not in out["mem"]
    assert out["psi"]["cpu_some"] == {"avg10": 1.5, "avg60": 1.0, "avg300": 0.5}
    assert (out["swap_in_pages"], out["swap_out_pages"], out["oom_kills"]) == (3, 4, 1)


def test_io_and_net_reports_deltas_over_the_window_and_skips_loop_and_lo(proc):
    def advance(_secs):
        (proc / "diskstats").write_text("   8       0 sda 110 0 0 70 210 0 0 120 0 800 0 0 0 0 0 0 0\n")
        (proc / "net" / "dev").write_text(
            "h\nh\n  eth0: 6144 1 0 0 0 0 0 0 7168 1 0 0 0 0 0 0\n")
        (proc / "net" / "snmp").write_text("Tcp: OutSegs RetransSegs\nTcp: 1200 7\n")

    out = perf_probe.io_and_net(str(proc), secs=1.0, sleep=advance)
    assert out["disks"] == {"sda": {"iops": 20.0, "await_ms": 2.0, "util_pct": 50.0}}
    assert out["nics"] == {"eth0": {"rx_kbps": 5.0, "tx_kbps": 5.0, "errs_drops": 0}}
    assert out["tcp_retrans_pct"] == 1.0


def test_units_parses_one_show_call_for_every_radon_unit():
    calls = []

    def run(argv, timeout=None):
        calls.append(argv)
        if argv[1] == "list-units":
            return "radon-api.service loaded active running X\nradon-relay.service loaded active running Y\n"
        return ("ActiveState=active\nNRestarts=2\nMemoryCurrent=1048576\nMemoryPeak=[not set]\n\n"
                "ActiveState=failed\nNRestarts=0\nCPUUsageNSec=18446744073709551615\n")

    out = perf_probe.units(run)
    assert out["radon-api.service"] == {"ActiveState": "active", "NRestarts": 2, "MemoryCurrent": 1048576}
    assert out["radon-relay.service"] == {"ActiveState": "failed", "NRestarts": 0}
    assert len(calls) == 2 and calls[1][:2] == ["systemctl", "show"]


def test_journal_emits_counts_and_collapsed_templates_never_raw_lines():
    lines = [
        {"_SYSTEMD_UNIT": "radon-api.service", "MESSAGE": "slow query 1234 ms on 0xdeadbeef"},
        {"_SYSTEMD_UNIT": "radon-api.service", "MESSAGE": "slow query 99 ms on 0xabc12345"},
        {"_SYSTEMD_UNIT": "radon-relay.service", "MESSAGE": [1, 2]},
    ]

    def run(argv, timeout=None):
        assert "-p" in argv and argv[argv.index("-p") + 1] == "warning"
        return "\n".join(json.dumps(x) for x in lines) + "\nnot json\n"

    out = perf_probe.journal(run)
    assert out["per_unit"] == {"radon-api.service": 2}
    assert out["top"] == [{"unit": "radon-api.service", "count": 2, "template": "slow query # ms on #"}]


def test_journal_templates_carry_no_credential_shaped_text():
    secret = "Zq" + "8vT3kLm9" * 3
    lines = [
        {"_SYSTEMD_UNIT": "radon-api.service", "MESSAGE": f"upstream rejected key {secret}"},
        {"_SYSTEMD_UNIT": "radon-api.service", "MESSAGE": f"GET https://u:pw{secret}@h.example/x?token={secret}&a=b failed"},
        {"_SYSTEMD_UNIT": "radon-api.service", "MESSAGE": f"Authorization: Bearer {secret[:12]} refused"},
        {"_SYSTEMD_UNIT": "radon-api.service", "MESSAGE": "password=hunter2xyz for ops@example.com"},
        {"_SYSTEMD_UNIT": "radon-api.service", "MESSAGE": 'bad body {"password":"hunter3", "api_key": \'shortkey\'}'},
    ]

    def run(argv, timeout=None):
        return "\n".join(json.dumps(x) for x in lines)

    out = perf_probe.journal(run)
    templates = " | ".join(row["template"] for row in out["top"])
    for leaked in (secret, secret[:12], "u:pw", "hunter2xyz", "ops@example.com", "a=b", "hunter3", "shortkey"):
        assert leaked not in templates, templates
    assert "upstream rejected key #" in templates
    assert "GET https://#@h.example/x?# failed" in templates
    assert out["per_unit"] == {"radon-api.service": 5}


def test_http_latency_reports_percentiles_status_and_errors():
    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def opener(req, timeout):
        assert req.get_method() == "GET"
        if "down" in req.full_url:
            raise ConnectionRefusedError()
        return Resp()

    out = perf_probe.http_latency((("up", "http://127.0.0.1:1/up"), ("down", "http://127.0.0.1:1/down")),
                                  samples=3, opener=opener)
    assert out["up"]["status"] == 200 and out["up"]["n"] == 3 and out["up"]["p50_ms"] is not None
    assert out["down"] == {"url": "http://127.0.0.1:1/down", "status": None, "bytes": None, "n": 0,
                           "p50_ms": None, "p95_ms": None, "max_ms": None, "error": "ConnectionRefusedError"}


def test_a_failing_section_is_reported_and_the_rest_still_run():
    def boom():
        raise OSError("no /proc/pressure")

    out = perf_probe.collect((("bad", boom), ("good", lambda: {"x": 1})))
    assert out["errors"] == {"bad": "OSError: no /proc/pressure"}
    assert out["good"] == {"x": 1}
    assert set(out["section_ms"]) == {"bad", "good"}
    assert out["schema"] == perf_probe.SCHEMA


def test_http_targets_are_loopback_and_never_the_mutating_health_route():
    for _name, url in perf_probe.HTTP_TARGETS:
        assert url.startswith("http://127.0.0.1:")
        assert not url.endswith(":8321/health"), "/health can trigger an IB reconnect"


def test_probe_is_stdlib_only_and_never_reads_env_files_or_mutates():
    source = PROBE.read_text()
    tree = ast.parse(source)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names)
    for forbidden in ("/etc/radon/env", "'.env'", "sudo", "py-spy", "os.kill", "Popen", "os.environ[",
                      '"w"', "unlink", "remove("):
        assert forbidden not in source, forbidden
    systemctl_verbs = {
        n.elts[1].value for n in ast.walk(tree)
        if isinstance(n, ast.List) and len(n.elts) > 1 and isinstance(n.elts[0], ast.Constant)
        and n.elts[0].value == "systemctl"
    }
    assert systemctl_verbs == {"list-units", "show"}


def test_main_prints_one_json_object(monkeypatch, capsys):
    monkeypatch.setattr(perf_probe, "SECTIONS", (("host", lambda: {"ok": True}),))
    assert perf_probe.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["host"] == {"ok": True}


def test_probe_compiles_on_the_hosts_python():
    subprocess.run([sys.executable, "-I", "-m", "py_compile", str(PROBE)], check=True)
