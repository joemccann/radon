"""DUR-14 — every service_health writer must be EXPLICITLY registered in
web/lib/serviceHealthWindows.ts.

Unregistered writers silently fall through to the 1h DEFAULT_WINDOW, which
fires the degraded banner overnight + on weekends (serviceHealthWindows.ts
was re-fixed 12+ times in 12 days for exactly this drift class). This test
closes the loop: a writer cannot ship without a deliberate freshness window.

Collected name sources (the four sanctioned write paths):
  1. ``service_cycle("<name>", …)`` call sites — standalone writers;
  2. ``service_name = "<name>"`` declared on BaseHandler subclasses —
     monitor-daemon handlers (structural heartbeat in BaseHandler.run);
  3. ``db.scan_mirror.SNAPSHOT_UPSERTS`` keys — mirror-fed scans;
  4. direct ``record_service_health("<name>", …)`` call sites — fetchers
     that bypass ``service_cycle`` (Equibles, Cboe, event-odds).

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

# Cache the rglob walk AND the parsed ASTs once at import time so each
# collector does not repeat the filesystem traversal or AST parse.
# Immutable after module load — safe to share across test functions.
_PYTHON_FILES: list[Path] = [
    path
    for path in _SCRIPTS_DIR.rglob("*.py")
    if not _SKIP_DIRS.intersection(path.relative_to(_SCRIPTS_DIR).parts)
]


def _parse_uncached(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


# (path, tree) pairs; tree is None for files that failed to parse.
_PARSED_TREES: list[tuple[Path, ast.Module | None]] = [
    (path, _parse_uncached(path)) for path in _PYTHON_FILES
]


def _python_files():
    return _PYTHON_FILES


def _parse(path: Path) -> ast.Module | None:
    """Return the cached AST for path, or None if it did not parse."""
    for p, tree in _PARSED_TREES:
        if p == path:
            return tree
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


def _build_service_cycle_names() -> dict[str, list[str]]:
    """``service_cycle(<name>, …)`` first args (string literals or
    module-level constants), name → files."""
    found: dict[str, list[str]] = {}
    for path, tree in _PARSED_TREES:
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


# Pre-compute all collector results once at module load so every test
# reads from the cache rather than re-walking the AST forest.
_SERVICE_CYCLE_NAMES: dict[str, list[str]] = _build_service_cycle_names()


def collect_service_cycle_names() -> dict[str, list[str]]:
    """Return cached ``service_cycle(<name>, …)`` first-arg names → files."""
    return _SERVICE_CYCLE_NAMES


def _build_scheduled_handler_classes() -> set[str]:
    """Handler classes `create_daemon()` actually registers.

    A handler class that nothing registers writes no heartbeat, so demanding
    a freshness window for it would force both catalogs to advertise a
    control that never runs (R-141, `ExitOrdersHandler`).
    """
    tree = _parse(_SCRIPTS_DIR / "monitor_daemon" / "run.py")
    assert tree is not None, "monitor_daemon/run.py must parse"
    registered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "register"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                registered.add(arg.func.id)
    assert registered, "failed to parse create_daemon() registrations"
    return registered


_SCHEDULED_HANDLER_CLASSES: set[str] = _build_scheduled_handler_classes()


def _build_handler_service_names(
    *, scheduled_only: bool = True
) -> dict[str, list[str]]:
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
            if scheduled_only and node.name not in _SCHEDULED_HANDLER_CLASSES:
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


_HANDLER_SERVICE_NAMES: dict[str, list[str]] = _build_handler_service_names()


def collect_handler_service_names() -> dict[str, list[str]]:
    """Return cached class-level ``service_name`` literals → files."""
    return _HANDLER_SERVICE_NAMES


def _build_scan_mirror_names() -> set[str]:
    from db.scan_mirror import SNAPSHOT_UPSERTS
    return set(SNAPSHOT_UPSERTS.keys())


_SCAN_MIRROR_NAMES: set[str] = _build_scan_mirror_names()


def collect_scan_mirror_names() -> set[str]:
    """Return cached SNAPSHOT_UPSERTS key set."""
    return _SCAN_MIRROR_NAMES


# Direct ``record_service_health`` wrappers / chokepoints — first arg is
# not a service name (state, a parameter, or ``self.service_name``).
_DIRECT_RECORD_EXCLUDES = frozenset({
    "db/writer.py",
    "db/service_cycle.py",
    "db/scan_mirror.py",
    "monitor_daemon/handlers/base.py",
    "ib_watchdog.py",
})


def _build_direct_record_service_health_names() -> dict[str, list[str]]:
    """``record_service_health(<name>, …)`` first args (string literals or
    module-level constants), name → files."""
    found: dict[str, list[str]] = {}
    for path, tree in _PARSED_TREES:
        if tree is None:
            continue
        if path.relative_to(_SCRIPTS_DIR).as_posix() in _DIRECT_RECORD_EXCLUDES:
            continue
        constants = _module_str_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if callee != "record_service_health":
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


_DIRECT_RECORD_NAMES: dict[str, list[str]] = _build_direct_record_service_health_names()


def collect_direct_record_service_health_names() -> dict[str, list[str]]:
    """Return cached ``record_service_health(<name>, …)`` first-arg names → files."""
    return _DIRECT_RECORD_NAMES


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
            "journal-sync",
            "flex-token-check",
            "preset-rebalance",
        }
        missing = expected - names
        assert not missing, f"handler collector lost: {sorted(missing)}"
        # `exit-orders` was in this set until R-141: the class exists and
        # still carries its service_name, but `create_daemon()` does not
        # register it, so it writes no heartbeat and must not be demanded of
        # either catalog. The class-level literal is still discoverable.
        # `cash-flow-sync` left the same way on 2026-09-02: the sFTP ingest
        # (flex_delivery_ingest) writes the row directly now.
        assert "exit-orders" not in names
        assert "cash-flow-sync" not in names
        assert "exit-orders" in _build_handler_service_names(scheduled_only=False)

    def test_scan_mirror_collector_sees_mirror_fed_scans(self):
        names = collect_scan_mirror_names()
        assert {"vcg-scan", "scanner", "discover"} <= names

    def test_direct_health_collector_sees_hand_rolled_writers(self):
        names = set(collect_direct_record_service_health_names())
        expected = {
            "cor",
            "vixcor",
            "straddle",
            "vol-cone",
            "skew",
            "skew2d",
            "margin-debt",
            "yield-curve",
            "credit-spread",
            "catalysts",
            "informed-flow",
            "cash-flow-sync",
        }
        missing = expected - names
        assert not missing, (
            f"direct record_service_health collector lost writers: "
            f"{sorted(missing)}. Either a writer moved onto service_cycle "
            "or the seam was renamed without updating this collector."
        )
        leaked = {"ok", "error", "syncing", "paused"} & names
        assert not leaked, (
            f"direct collector invented state labels as services: {sorted(leaked)}. "
            "ib_watchdog.py (state-first) must stay in _DIRECT_RECORD_EXCLUDES."
        )


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
        for name, files in collect_direct_record_service_health_names().items():
            if name not in registered:
                unregistered.append(f"{name} (record_service_health: {files[0]})")

        assert not unregistered, (
            "Writers without an explicit SERVICE_FRESHNESS_WINDOWS entry "
            "(they'd silently inherit the 1h default and flap the banner "
            "overnight):\n  " + "\n  ".join(sorted(unregistered)) +
            "\nRegister each in web/lib/serviceHealthWindows.ts with a "
            "deliberate window + category + requires_ib."
        )


# ── T-163: a registered producer must never die before its heartbeat ──
#
# Registration (above) only buys a freshness window. A producer that dies
# BEFORE it ever calls its health writer leaves no row at all, and a row that
# never arrives is silent rather than stale: the banner shows an outage the
# watchdog cannot attribute. The canonical instance is API-client
# construction — `EquiblesClient()` raises `EquiblesAuthError` from
# `__init__` when the key is unset or rejected — sitting above the try that
# owns health reporting.

_HEALTH_WRITE_CALL = "record_service_health"
_SERVICE_CONSTANTS = {"SERVICE", "SERVICE_NAME"}
_CLIENT_CTOR = re.compile(r"^[A-Z]\w*Client$")


def _called_names(node: ast.AST) -> set[str]:
    """Every callee name reachable under `node` (bare and attribute calls)."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _declares_service_constant(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(t, ast.Name) and t.id in _SERVICE_CONSTANTS for t in node.targets
            ):
                return True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id in _SERVICE_CONSTANTS:
                return True
    return False


def _health_writer_functions(tree: ast.Module) -> set[str]:
    """Names of defs whose own body writes a `service_health` row."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _HEALTH_WRITE_CALL in _called_names(node)
    }


def _enclosing_function(tree: ast.Module) -> dict[int, str]:
    """id(node) -> name of the innermost def containing it."""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owner[id(child)] = node.name
    return owner


def _health_guarded_try_blocks(tree: ast.Module, health_fns: set[str]) -> list[ast.Try]:
    """Try nodes with an `except` handler that writes a health row."""
    guarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            called = _called_names(handler)
            if _HEALTH_WRITE_CALL in called or called & health_fns:
                guarded.append(node)
                break
    return guarded


def _client_construction_sites(tree: ast.Module) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and _CLIENT_CTOR.match(node.func.id)
    ]


def _unguarded_client_constructions(tree: ast.Module) -> list[str]:
    """Client constructions with no health row between them and the exit."""
    health_fns = _health_writer_functions(tree)
    guarded_blocks = _health_guarded_try_blocks(tree, health_fns)

    protected_nodes: set[int] = set()
    protected_callees: set[str] = set()
    for block in guarded_blocks:
        for stmt in block.body:
            for child in ast.walk(stmt):
                protected_nodes.add(id(child))
            protected_callees |= _called_names(stmt)

    owner = _enclosing_function(tree)
    violations: list[tuple[str, int, str]] = []
    for call in _client_construction_sites(tree):
        if id(call) in protected_nodes:
            continue
        # Indirect: the ctor lives in a helper (`_resolve_client`) or in
        # `run()`, and the CALLER wraps it in a health-guarded try.
        if owner.get(id(call)) in protected_callees:
            continue
        violations.append(
            (owner.get(id(call), "<module>"), call.lineno, call.func.id)
        )
    return violations


# Pre-existing sites, recorded 2026-08-26 with the check that found them.
# NOT sanctioned — they are the same silent-death shape as T-163, just in
# producers T-163 did not scope. This list may shrink, never grow: fix the
# site and delete its entry. `test_the_baseline_has_no_stale_entries` fails
# if an entry is fixed but left here.
_UNGUARDED_CTOR_BASELINE = {
    "scripts/fetch_credit_spread.py::fetch_uw_closes",
    "scripts/fetch_iei_hyg.py::fetch_uw_closes",
    "scripts/fetch_ivrank.py::_real_ib_fetch",
    "scripts/fetch_trin.py::sample_live",
    "scripts/fetch_vixcor.py::run",
    "scripts/ib_reconcile.py::connect_ib",
}


class TestRegisteredProducersHeartbeatBeforeDying:
    """Static structure check: no API client is built outside a health block.

    WHAT IT CATCHES: a module that declares `SERVICE`/`SERVICE_NAME` and owns
    a `record_service_health` writer, but constructs a `*Client` where no
    enclosing `try` has an `except` handler that writes a health row — either
    directly, or one level up via the function the ctor lives in. That is the
    exact T-163/`3b2945b7` regression shape.

    WHAT IT CANNOT CATCH: it is AST-only and one caller-hop deep. It does not
    prove the handler writes the RIGHT service name or an `error` state, does
    not follow a ctor buried two helpers down, does not model dynamic
    factories or `getattr` construction, and says nothing about non-ctor
    early exits (`sys.exit`, `parser.error`) — the per-producer tests in
    scripts/tests/test_equibles_*.py own those behaviours at runtime.
    """

    def _producer_modules(self):
        return [
            (path, tree)
            for path, tree in _PARSED_TREES
            if tree is not None
            and _declares_service_constant(tree)
            and _health_writer_functions(tree)
        ]

    def test_the_equibles_producers_are_all_in_scope(self):
        covered = {path.name for path, _ in self._producer_modules()}
        expected = {
            "fetch_equibles_smart_money_13f.py",
            "fetch_equibles_filing_forensics.py",
            "fetch_equibles_ats_venue_share.py",
            "fetch_equibles_short_crowding.py",
            "fetch_equibles_cot_positioning.py",
        }
        assert expected <= covered, (
            "the collector stopped seeing Equibles producers: "
            f"{sorted(expected - covered)}"
        )

    def _offenders(self) -> dict[str, str]:
        """key -> human-readable site, for every unguarded construction."""
        found: dict[str, str] = {}
        for path, tree in self._producer_modules():
            rel = path.relative_to(_PROJECT_ROOT).as_posix()
            for func, lineno, ctor in _unguarded_client_constructions(tree):
                found[f"{rel}::{func}"] = f"{rel}:{lineno} {ctor}() in {func}()"
        return found

    def test_no_producer_constructs_its_client_outside_the_health_block(self):
        new = {
            key: site
            for key, site in self._offenders().items()
            if key not in _UNGUARDED_CTOR_BASELINE
        }
        assert not new, (
            "Client construction outside the health-reporting block. An unset "
            "or rejected API key raises in __init__, so these oneshots die "
            "writing NO service_health row at all — silent, not stale, and "
            "nothing alerts:\n  " + "\n  ".join(sorted(new.values())) +
            "\nMove the construction inside a try whose except handler "
            "records an `error` heartbeat and re-raises."
        )

    def test_the_baseline_has_no_stale_entries(self):
        stale = _UNGUARDED_CTOR_BASELINE - set(self._offenders())
        assert not stale, (
            "These baseline entries no longer construct outside the health "
            f"block: {sorted(stale)}. Delete them — the list may shrink, "
            "never grow."
        )
