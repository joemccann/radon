"""DUR-14 — every service_health writer must be EXPLICITLY registered in
web/lib/serviceHealthWindows.ts.

Unregistered writers silently fall through to the 1h DEFAULT_WINDOW, which
fires the degraded banner overnight + on weekends (serviceHealthWindows.ts
was re-fixed 12+ times in 12 days for exactly this drift class). This test
closes the loop: a writer cannot ship without a deliberate freshness window.

Collected name sources (the three sanctioned write paths):
  1. ``service_cycle("<name>", …)`` call sites — standalone writers;
  2. ``service_name = "<name>"`` declared on BaseHandler subclasses —
     monitor-daemon handlers (structural heartbeat in BaseHandler.run);
  3. ``db.scan_mirror.SNAPSHOT_UPSERTS`` keys — mirror-fed scans.

If this test finds a genuinely unregistered writer, REGISTER it in the TS
file with a sensible window — do not weaken the collector.

Out of scope (residual, hand-written rows): JS writers (ib_realtime_server
relay, writer.js consumers), scripts/ib_watchdog.py, scripts/watchdog
(watchdog-alerts), replica_watchdog's bespoke event-driven rows, and the
radon-cloud VPS writers (deploy, config-drift, db-backup) — all already
registered by hand in the TS file.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
_TS_FILE = _PROJECT_ROOT / "web" / "lib" / "serviceHealthWindows.ts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_SKIP_DIRS = {"tests", "__pycache__", "node_modules", ".venv"}


def _python_files():
    for path in _SCRIPTS_DIR.rglob("*.py"):
        if not _SKIP_DIRS.intersection(path.relative_to(_SCRIPTS_DIR).parts):
            yield path


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


def _module_str_constants(tree: ast.Module) -> dict[str, str]:
    """Top-level ``NAME = "literal"`` assignments (gamma_rotation_gap passes
    its service name via a module constant)."""
    constants: dict[str, str] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not (isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = stmt.value.value
    return constants


def collect_service_cycle_names() -> dict[str, list[str]]:
    """``service_cycle(<name>, …)`` first args (string literals or
    module-level constants), name → files."""
    found: dict[str, list[str]] = {}
    for path in _python_files():
        tree = _parse(path)
        if tree is None:
            continue
        constants = _module_str_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if callee not in {"service_cycle", "record_failed_cycle"}:
                continue
            if not node.args:
                continue
            arg = node.args[0]
            name: str | None = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                name = arg.value
            elif isinstance(arg, ast.Name):
                name = constants.get(arg.id)
            if name:
                found.setdefault(name, []).append(str(path))
    return found


def collect_handler_service_names() -> dict[str, list[str]]:
    """Class-level ``service_name = "<literal>"`` on handler classes."""
    found: dict[str, list[str]] = {}
    handlers_dir = _SCRIPTS_DIR / "monitor_daemon" / "handlers"
    for path in handlers_dir.glob("*.py"):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
                if "service_name" not in targets and "_SERVICE_NAME" not in targets:
                    continue
                if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                    found.setdefault(stmt.value.value, []).append(str(path))
    return found


def collect_scan_mirror_names() -> set[str]:
    from db.scan_mirror import SNAPSHOT_UPSERTS

    return set(SNAPSHOT_UPSERTS.keys())


def ts_registered_services() -> set[str]:
    """All explicit keys of SERVICE_FRESHNESS_WINDOWS (any category)."""
    text = _TS_FILE.read_text(encoding="utf-8")
    marker = "SERVICE_FRESHNESS_WINDOWS"
    start = text.index(marker)
    end = text.index("\n};", start)
    block = text[start:end]
    return set(re.findall(r'^\s*"([a-z][a-z0-9\-]*)"\s*:\s*\{', block, re.MULTILINE))


class TestCollectorsAreNotBlind:
    """Sentinel assertions: if a refactor renames the seams, the collectors
    must fail loudly instead of silently collecting nothing."""

    def test_service_cycle_collector_sees_migrated_writers(self):
        names = set(collect_service_cycle_names())
        expected = {
            "cri-scan",
            "gex-scan",
            "portfolio-sync",
            "orders-sync",
            "cta-sync",
            "llm-token-index",
            "gamma-rotation-scan",
            "analyst-ratings",
        }
        missing = expected - names
        assert not missing, (
            f"service_cycle collector lost writers: {sorted(missing)}. "
            "Either a writer regressed to hand-rolled record_service_health "
            "or the seam was renamed without updating this collector."
        )

    def test_handler_collector_sees_daemon_handlers(self):
        names = set(collect_handler_service_names())
        expected = {
            "fill-monitor",
            "exit-orders",
            "journal-sync",
            "flex-token-check",
            "cash-flow-sync",
            "preset-rebalance",
        }
        missing = expected - names
        assert not missing, f"handler collector lost: {sorted(missing)}"

    def test_scan_mirror_collector_sees_mirror_fed_scans(self):
        names = collect_scan_mirror_names()
        assert {"vcg-scan", "scanner", "discover"} <= names


class TestEveryWriterIsRegistered:
    def test_no_writer_falls_through_to_the_default_window(self):
        registered = ts_registered_services()
        assert registered, "failed to parse SERVICE_FRESHNESS_WINDOWS keys"

        unregistered: list[str] = []
        for name, files in collect_service_cycle_names().items():
            if name not in registered:
                unregistered.append(f"{name} (service_cycle: {files[0]})")
        for name, files in collect_handler_service_names().items():
            if name not in registered:
                unregistered.append(f"{name} (BaseHandler: {files[0]})")
        for name in collect_scan_mirror_names():
            if name not in registered:
                unregistered.append(f"{name} (scan_mirror)")

        assert not unregistered, (
            "Writers without an explicit SERVICE_FRESHNESS_WINDOWS entry "
            "(they'd silently inherit the 1h default and flap the banner "
            "overnight):\n  " + "\n  ".join(sorted(unregistered)) +
            "\nRegister each in web/lib/serviceHealthWindows.ts with a "
            "deliberate window + category + requires_ib."
        )
