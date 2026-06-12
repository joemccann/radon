"""DUR-01 — scans own their Turso snapshot + service_health heartbeat.

The FastAPI-side mirror (``server._maybe_dual_write_to_db``) is gone: its
synchronous libsql writes starved the event loop even from a worker thread
(libsql holds the GIL during a hung commit). Each scan subprocess now writes
its own snapshot + heartbeat via ``db.scan_mirror.mirror_scan_snapshot`` —
the subprocess has its own GIL, so a hung Turso write can never stall the API.

Two layers of coverage:
  - mirror semantics: ok row on success, error row when the upsert fails,
    never raises, never touches the real DB (db.writer is monkeypatched —
    see feedback_test_pollution_to_production).
  - per-scan wiring: each migrated scan records its own service name on its
    success output path, and an error row when its snapshot upsert fails.
"""
from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

UPSERT_NAMES = [
    "upsert_vcg_snapshot",
    "upsert_scanner_snapshot",
    "upsert_discover_snapshot",
    "upsert_flow_analysis_snapshot",
    "upsert_performance_snapshot",
    "upsert_oi_changes",
]


def _resolved_writer():
    """The module ``from db import writer`` resolves to at mirror call time.

    Other test files (test_replica_watchdog, the monitor-daemon heartbeat
    tests) permanently swap a bare stub in for ``db.writer``; patching the
    resolved module — whichever it is — keeps these tests order-independent
    without mutating global sys.modules state ourselves.
    """
    db_pkg = sys.modules.get("db")
    writer = getattr(db_pkg, "writer", None) if db_pkg is not None else None
    if writer is None:
        import db.writer as writer
    return writer


@pytest.fixture
def writer_calls(monkeypatch):
    """Stub every db.writer surface the mirror touches; record call shapes."""
    writer = _resolved_writer()
    calls = {"health": [], "upserts": []}

    def fake_health(service, state, **kwargs):
        calls["health"].append((service, state, kwargs))

    monkeypatch.setattr(writer, "record_service_health", fake_health, raising=False)
    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None, raising=False)
    for name in UPSERT_NAMES:
        def fake_upsert(*args, _name=name, **kwargs):
            calls["upserts"].append((_name, args))
        monkeypatch.setattr(writer, name, fake_upsert, raising=False)
    return calls


def _make_upsert_raise(monkeypatch, upsert_name):
    def boom(*args, **kwargs):
        raise RuntimeError("turso down")
    monkeypatch.setattr(_resolved_writer(), upsert_name, boom, raising=False)


def _ok_rows(calls):
    return [(svc, kw) for svc, state, kw in calls["health"] if state == "ok"]


def _error_rows(calls):
    return [(svc, kw) for svc, state, kw in calls["health"] if state == "error"]


# ── mirror_scan_snapshot semantics ───────────────────────────────────


class TestMirrorScanSnapshot:
    def test_ok_row_and_snapshot_on_success(self, writer_calls):
        from db.scan_mirror import mirror_scan_snapshot

        payload = {"scan_time": "2026-06-12T14:00:00+00:00", "signal": "NONE"}
        mirror_scan_snapshot("vcg-scan", payload)

        assert writer_calls["upserts"] == [
            ("upsert_vcg_snapshot", ("2026-06-12T14:00:00+00:00", payload))
        ]
        assert writer_calls["health"] == [
            ("vcg-scan", "ok", {"finished_at": "2026-06-12T14:00:00+00:00"})
        ]

    def test_error_row_when_upsert_fails(self, writer_calls, monkeypatch):
        from db.scan_mirror import mirror_scan_snapshot

        _make_upsert_raise(monkeypatch, "upsert_scanner_snapshot")
        mirror_scan_snapshot("scanner", {"scan_time": "T"})  # must not raise

        assert _ok_rows(writer_calls) == []
        [(service, kwargs)] = _error_rows(writer_calls)
        assert service == "scanner"
        assert "turso down" in kwargs["error"]["detail"]

    def test_never_raises_even_when_health_write_fails(self, writer_calls, monkeypatch):
        import db.writer as writer
        from db.scan_mirror import mirror_scan_snapshot

        def boom(*args, **kwargs):
            raise RuntimeError("turso down")

        monkeypatch.setattr(writer, "record_service_health", boom)
        mirror_scan_snapshot("leap-scan", {"scan_time": "T"})  # must not raise

    def test_heartbeat_only_services_skip_snapshot(self, writer_calls):
        from db.scan_mirror import mirror_scan_snapshot

        mirror_scan_snapshot("leap-scan", {"scan_time": "T1"})
        mirror_scan_snapshot("garch-scan", {"scan_time": "T2"})

        assert writer_calls["upserts"] == []
        assert writer_calls["health"] == [
            ("leap-scan", "ok", {"finished_at": "T1"}),
            ("garch-scan", "ok", {"finished_at": "T2"}),
        ]

    def test_unknown_service_is_a_programmer_error(self, writer_calls):
        from db.scan_mirror import mirror_scan_snapshot

        with pytest.raises(ValueError):
            mirror_scan_snapshot("not-a-scan", {})

    def test_missing_scan_time_falls_back_to_et_date(self, writer_calls):
        from db.scan_mirror import mirror_scan_snapshot

        mirror_scan_snapshot("flow-analysis", {"analysis_time": "ignored"})

        [(name, args)] = writer_calls["upserts"]
        assert name == "upsert_flow_analysis_snapshot"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", args[0])
        # finished_at stays None so COALESCE keeps any prior value
        assert writer_calls["health"] == [("flow-analysis", "ok", {"finished_at": None})]

    def test_taken_at_overrides_payload_scan_time(self, writer_calls):
        from db.scan_mirror import mirror_scan_snapshot

        mirror_scan_snapshot("performance", {"scan_time": "wrong"}, taken_at="2026-06-12T15:00:00Z")

        [(name, args)] = writer_calls["upserts"]
        assert name == "upsert_performance_snapshot"
        assert args[0] == "2026-06-12T15:00:00Z"


# ── per-scan wiring ──────────────────────────────────────────────────


class TestScannerWiring:
    def _run(self, tmp_path, monkeypatch):
        import scanner

        watchlist = tmp_path / "watchlist.json"
        watchlist.write_text(json.dumps({"tickers": []}))
        monkeypatch.setattr(scanner, "WATCHLIST", watchlist)
        monkeypatch.setattr(scanner, "get_open_positions", lambda: set())
        with redirect_stdout(io.StringIO()):
            scanner.scan(top_n=5)

    def test_records_ok_row(self, tmp_path, monkeypatch, writer_calls):
        self._run(tmp_path, monkeypatch)
        [(service, _)] = _ok_rows(writer_calls)
        assert service == "scanner"
        assert writer_calls["upserts"][0][0] == "upsert_scanner_snapshot"

    def test_records_error_row_when_upsert_fails(self, tmp_path, monkeypatch, writer_calls):
        _make_upsert_raise(monkeypatch, "upsert_scanner_snapshot")
        self._run(tmp_path, monkeypatch)
        [(service, _)] = _error_rows(writer_calls)
        assert service == "scanner"


class TestDiscoverWiring:
    def _run(self, monkeypatch, result):
        import discover

        monkeypatch.setattr(discover, "discover", lambda **kwargs: result)
        monkeypatch.setattr(sys, "argv", ["discover.py"])
        with redirect_stdout(io.StringIO()):
            discover.main()

    def test_records_ok_row(self, monkeypatch, writer_calls):
        self._run(monkeypatch, {"discovery_time": "T", "candidates": []})
        [(service, _)] = _ok_rows(writer_calls)
        assert service == "discover"
        assert writer_calls["upserts"][0][0] == "upsert_discover_snapshot"

    def test_records_error_row_when_upsert_fails(self, monkeypatch, writer_calls):
        _make_upsert_raise(monkeypatch, "upsert_discover_snapshot")
        self._run(monkeypatch, {"discovery_time": "T", "candidates": []})
        [(service, _)] = _error_rows(writer_calls)
        assert service == "discover"

    def test_error_payload_skips_heartbeat(self, monkeypatch, writer_calls):
        # The server returns 400 + skips the cache for {"error": ...} payloads;
        # the scan must not heartbeat ok for a failed discovery either.
        self._run(monkeypatch, {"error": "UW unavailable"})
        assert writer_calls["health"] == []


class TestFlowAnalysisWiring:
    def _run(self, monkeypatch):
        import flow_analysis

        monkeypatch.setattr(flow_analysis, "load_portfolio", lambda: [])
        with redirect_stdout(io.StringIO()):
            flow_analysis.run_analysis()

    def test_records_ok_row_even_on_empty_portfolio(self, monkeypatch, writer_calls):
        # Heartbeat on EVERY cycle, including the empty short-circuit
        # (feedback_service_health_heartbeat).
        self._run(monkeypatch)
        [(service, _)] = _ok_rows(writer_calls)
        assert service == "flow-analysis"
        assert writer_calls["upserts"][0][0] == "upsert_flow_analysis_snapshot"

    def test_records_error_row_when_upsert_fails(self, monkeypatch, writer_calls):
        _make_upsert_raise(monkeypatch, "upsert_flow_analysis_snapshot")
        self._run(monkeypatch)
        [(service, _)] = _error_rows(writer_calls)
        assert service == "flow-analysis"


class TestPerformanceWiring:
    def _run(self, monkeypatch):
        import portfolio_performance

        monkeypatch.setattr(portfolio_performance, "build_payload", lambda **kwargs: {})
        with redirect_stdout(io.StringIO()):
            portfolio_performance.main(["--json"])

    def test_records_ok_row(self, monkeypatch, writer_calls):
        self._run(monkeypatch)
        [(service, _)] = _ok_rows(writer_calls)
        assert service == "performance"
        assert writer_calls["upserts"][0][0] == "upsert_performance_snapshot"

    def test_records_error_row_when_upsert_fails(self, monkeypatch, writer_calls):
        _make_upsert_raise(monkeypatch, "upsert_performance_snapshot")
        self._run(monkeypatch)
        [(service, _)] = _error_rows(writer_calls)
        assert service == "performance"


class TestOiChangesWiring:
    def _run(self, monkeypatch, argv):
        import fetch_oi_changes

        monkeypatch.setattr(fetch_oi_changes, "fetch_market_oi_changes", lambda **kwargs: [])
        monkeypatch.setattr(fetch_oi_changes, "fetch_ticker_oi_changes", lambda *a, **kw: [])
        monkeypatch.setattr(sys, "argv", ["fetch_oi_changes.py"] + argv)
        with redirect_stdout(io.StringIO()):
            fetch_oi_changes.main()

    def test_market_scan_records_ok_row(self, monkeypatch, writer_calls):
        self._run(monkeypatch, ["--market", "--json"])
        [(service, _)] = _ok_rows(writer_calls)
        assert service == "oi-changes"
        assert writer_calls["upserts"][0][0] == "upsert_oi_changes"

    def test_market_scan_records_error_row_when_upsert_fails(self, monkeypatch, writer_calls):
        _make_upsert_raise(monkeypatch, "upsert_oi_changes")
        self._run(monkeypatch, ["--market", "--json"])
        [(service, _)] = _error_rows(writer_calls)
        assert service == "oi-changes"

    def test_single_ticker_fetch_does_not_heartbeat(self, monkeypatch, writer_calls):
        # Per-ticker evaluation lookups are not the scheduled scan; a
        # heartbeat here would mask a dead market-wide scan.
        self._run(monkeypatch, ["MSFT", "--json"])
        assert writer_calls["health"] == []


class TestVcgWiring:
    def _run(self, monkeypatch, cached):
        import vcg_scan
        import utils.scan_cache_gate as gate

        monkeypatch.setattr(vcg_scan, "is_market_open", lambda: False)
        monkeypatch.setattr(gate, "cached_scan_if_fresh", lambda *a, **kw: cached)
        monkeypatch.setattr(sys, "argv", ["vcg_scan.py", "--json"])
        with redirect_stdout(io.StringIO()):
            vcg_scan.main()

    def test_cached_serve_records_ok_row(self, monkeypatch, writer_calls):
        # The off-hours cached serve must heartbeat too — the FastAPI-side
        # mirror used to heartbeat on every cached payload it re-wrote.
        self._run(monkeypatch, {"credit_proxy": "HYG", "scan_time": "T"})
        [(service, _)] = _ok_rows(writer_calls)
        assert service == "vcg-scan"
        assert writer_calls["upserts"][0][0] == "upsert_vcg_snapshot"

    def test_cached_serve_records_error_row_when_upsert_fails(self, monkeypatch, writer_calls):
        _make_upsert_raise(monkeypatch, "upsert_vcg_snapshot")
        self._run(monkeypatch, {"credit_proxy": "HYG", "scan_time": "T"})
        [(service, _)] = _error_rows(writer_calls)
        assert service == "vcg-scan"


class TestLeapWiring:
    def test_records_ok_row(self, tmp_path, monkeypatch, writer_calls):
        import leap_scanner_uw

        result = SimpleNamespace(
            ticker="AAPL",
            vol_data=SimpleNamespace(price=100.0, hv_20=20.0, hv_60=25.0, hv_252=30.0),
            current_iv=45.0,
            iv_rank=10.0,
            leaps=[],
            best_gap=15.0,
            is_mispriced=True,
        )
        monkeypatch.setattr(leap_scanner_uw, "scan_ticker", lambda *a, **kw: result)
        monkeypatch.setattr(leap_scanner_uw, "generate_report", lambda *a, **kw: "<html>")
        monkeypatch.setattr(leap_scanner_uw, "DASHBOARD_CACHE_PATH", tmp_path / "leap.json")
        monkeypatch.setattr(
            sys, "argv",
            ["leap_scanner_uw.py", "AAPL", "--json", "--output", str(tmp_path / "report.html")],
        )
        with redirect_stdout(io.StringIO()):
            leap_scanner_uw.main()

        assert writer_calls["upserts"] == []  # no leap table — file cache is canonical
        [(service, _)] = _ok_rows(writer_calls)
        assert service == "leap-scan"
        assert json.loads((tmp_path / "leap.json").read_text())["results"][0]["ticker"] == "AAPL"


class TestGarchWiring:
    def test_records_ok_row(self, tmp_path, monkeypatch, writer_calls):
        import garch_convergence

        pair = SimpleNamespace(
            all_gates_pass=False,
            ticker_a="NVDA",
            ticker_b="AMD",
            divergence=0.0,
            lagger_hv_iv_gap=0.0,
            signal="NONE",
        )
        monkeypatch.setattr(
            garch_convergence, "resolve_inputs",
            lambda tickers, preset: (["NVDA", "AMD"], [["NVDA", "AMD"]], "test", None),
        )
        monkeypatch.setattr(garch_convergence, "fetch_all_tickers", lambda *a, **kw: {})
        monkeypatch.setattr(garch_convergence, "analyze_pair", lambda *a, **kw: pair)
        monkeypatch.setattr(garch_convergence, "to_json", lambda *a, **kw: {"scan_time": "T"})
        monkeypatch.setattr(garch_convergence, "_PROJECT_DIR", tmp_path)
        monkeypatch.setattr(sys, "argv", ["garch_convergence.py", "--preset", "semis", "--json"])
        with redirect_stdout(io.StringIO()):
            garch_convergence.main()

        assert writer_calls["upserts"] == []  # no garch table — file cache is canonical
        [(service, _)] = _ok_rows(writer_calls)
        assert service == "garch-scan"
