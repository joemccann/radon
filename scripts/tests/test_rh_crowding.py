"""Robinhood crowding overlay — row building, storage, unconfigured skip,
and the hard rule that crowding can never trip the Four Gates.

The rh_crowding series (migration 0066) is descriptive retail-crowding
context only: popular-watchlist rank + scan hits from the read-only trading
MCP. Gate 1 (convexity), Gate 2 (dark-pool edge) and Gate 3 (fractional
Kelly, 2.5% cap) must be structurally unable to read it.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import fetch_rh_crowding as crowding  # noqa: E402

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0066_rh_crowding.sql"


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_with_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """In-memory sqlite with schema_migrations bootstrap + 0066 applied."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """
    )
    for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()

    import db.client as client_mod
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    import importlib

    import db.writer as writer_mod
    importlib.reload(writer_mod)

    try:
        yield conn
    finally:
        conn.close()


POPULAR = [
    {
        "name": "100 Most Popular",
        "items": [
            {"symbol": "TSLA"},
            {"symbol": "NVDA"},
            {"symbol": "AAPL"},
        ],
    },
    {"name": "Daily Movers", "items": [{"symbol": "NVDA"}, {"symbol": "GME"}]},
]
SCANS = {
    "scan-1": [{"symbol": "NVDA"}, {"symbol": "GME"}, {"symbol": "NVDA"}],
    "scan-2": [{"symbol": "NVDA"}],
}


class TestBuildRows:
    def test_rank_is_first_appearance_across_popular_watchlists(self):
        rows = {r["symbol"]: r for r in crowding.build_rows(POPULAR, {}, "2026-08-30")}
        assert rows["TSLA"]["popular_rank"] == 1
        assert rows["NVDA"]["popular_rank"] == 2
        assert rows["AAPL"]["popular_rank"] == 3
        assert rows["GME"]["popular_rank"] == 4
        assert rows["NVDA"]["watchlists"] == ["100 Most Popular", "Daily Movers"]

    def test_scan_hits_count_distinct_scans_not_duplicate_rows(self):
        rows = {r["symbol"]: r for r in crowding.build_rows(POPULAR, SCANS, "2026-08-30")}
        assert rows["NVDA"]["scan_hits"] == 2  # in both scans, dup row ignored
        assert rows["GME"]["scan_hits"] == 1
        assert rows["TSLA"]["scan_hits"] == 0

    def test_scan_only_symbols_get_rows_with_null_rank(self):
        rows = crowding.build_rows([], {"scan-1": [{"ticker": "AMC"}]}, "2026-08-30")
        assert rows == [{
            "date": "2026-08-30", "symbol": "AMC", "popular_rank": None,
            "watchlists": [], "scan_hits": 1,
        }]

    def test_unreadable_payload_shapes_yield_no_rows(self):
        assert crowding.build_rows(
            [{"weird": True}], {"scan-1": [{"no_symbol": 1}]}, "2026-08-30"
        ) == []

    def test_degenerate_payloads_are_capped_keeping_the_most_crowded(self):
        """A hostile/degenerate payload must not produce an unbounded Turso
        write (Hrana bounding) — the ranked (most crowded) names survive."""
        huge = [{
            "name": "hostile",
            "items": [{"symbol": f"S{i:05d}"} for i in range(crowding.MAX_ROWS_PER_LIST + 500)],
        }]
        rows = crowding.build_rows(huge, {}, "2026-08-30")

        assert len(rows) == crowding.MAX_CROWDING_ROWS
        kept = {r["symbol"] for r in rows}
        assert "S00000" in kept, "rank 1 must survive the cap"
        assert f"S{crowding.MAX_CROWDING_ROWS - 1:05d}" in kept
        assert f"S{crowding.MAX_CROWDING_ROWS:05d}" not in kept

    def test_watchlist_iteration_is_bounded(self):
        beyond = [{"name": f"w{i}", "items": [{"symbol": f"W{i}"}]}
                  for i in range(crowding.MAX_WATCHLISTS + 10)]
        rows = crowding.build_rows(beyond, {}, "2026-08-30")
        symbols = {r["symbol"] for r in rows}
        assert f"W{crowding.MAX_WATCHLISTS}" not in symbols, (
            "watchlists beyond the cap must not be iterated"
        )


class TestWriterUpsert:
    def test_rows_land_and_upsert_is_idempotent(self, db_with_schema):
        import db.writer as writer

        rows = crowding.build_rows(POPULAR, SCANS, "2026-08-30")
        writer.upsert_rh_crowding_rows(rows, recorded_at="2026-08-30T20:00:00Z")
        writer.upsert_rh_crowding_rows(rows, recorded_at="2026-08-30T21:00:00Z")

        stored = db_with_schema.execute(
            "SELECT date, symbol, popular_rank, watchlists, scan_hits, recorded_at "
            "FROM rh_crowding ORDER BY symbol"
        ).fetchall()
        assert len(stored) == 4
        nvda = next(row for row in stored if row[1] == "NVDA")
        assert nvda[2] == 2
        assert json.loads(nvda[3]) == ["100 Most Popular", "Daily Movers"]
        assert nvda[4] == 2
        assert nvda[5] == "2026-08-30T21:00:00Z"

    def test_empty_rows_write_nothing(self, db_with_schema):
        import db.writer as writer

        writer.upsert_rh_crowding_rows([])
        assert db_with_schema.execute("SELECT COUNT(*) FROM rh_crowding").fetchone()[0] == 0

    def test_bulk_writes_are_chunked_multirow_not_per_row(
        self, db_with_schema, monkeypatch
    ):
        """Hrana I/O bounding: per-row execute is one round-trip PER ROW
        (the rv-ratio 2026-07-21 502 incident class). 900 rows must land
        in ceil(900/400) = 3 INSERT statements, not 900."""
        import db.writer as writer

        insert_statements: list[str] = []
        real_execute = db_with_schema.execute

        def counting_execute(sql, *args):
            if sql.lstrip().upper().startswith("INSERT INTO RH_CROWDING"):
                insert_statements.append(sql)
            return real_execute(sql, *args)

        class _CountingConn:
            def execute(self, sql, *args):
                return counting_execute(sql, *args)

            def commit(self):
                return db_with_schema.commit()

        monkeypatch.setattr(writer, "get_db", lambda: _CountingConn())

        rows = [
            {"date": "2026-08-30", "symbol": f"T{i:04d}", "popular_rank": i + 1,
             "watchlists": ["popular"], "scan_hits": 0}
            for i in range(900)
        ]
        writer.upsert_rh_crowding_rows(rows, recorded_at="2026-08-30T20:00:00Z")

        assert len(insert_statements) == 3, (
            f"expected 3 chunked statements for 900 rows, got {len(insert_statements)}"
        )
        stored = db_with_schema.execute("SELECT COUNT(*) FROM rh_crowding").fetchone()[0]
        assert stored == 900


class TestUnconfiguredSkip:
    def test_run_skips_cleanly_without_token(self, monkeypatch):
        monkeypatch.delenv("ROBINHOOD_MCP_TOKEN", raising=False)
        monkeypatch.setattr(
            crowding, "fetch_crowding",
            lambda: (_ for _ in ()).throw(AssertionError("fetched while unconfigured")),
        )
        monkeypatch.setattr(
            crowding, "persist",
            lambda *a: (_ for _ in ()).throw(AssertionError("wrote while unconfigured")),
        )
        assert crowding.run() is None

    def test_main_exits_zero_when_unconfigured(self, monkeypatch, capsys):
        monkeypatch.delenv("ROBINHOOD_MCP_TOKEN", raising=False)
        monkeypatch.setattr(sys, "argv", ["fetch_rh_crowding.py"])
        crowding.main()  # must not raise / sys.exit non-zero
        assert "skipping cleanly" in capsys.readouterr().err


class TestConfiguredRun:
    def test_run_builds_and_persists_rows(self, monkeypatch):
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "tok")
        monkeypatch.setattr(crowding, "fetch_crowding", lambda: (POPULAR, SCANS))
        persisted: list = []
        monkeypatch.setattr(
            crowding, "persist", lambda rows, scan_time: persisted.append((rows, scan_time))
        )

        payload = crowding.run()

        assert payload is not None and payload["count"] == 4
        assert len(persisted) == 1


class TestFetchCrowdingExecutes:
    """fetch_crowding() driven for real against a RobinhoodClient double:
    the id/scan_id probing, the MAX_SCANS_PER_RUN slice, and the per-scan
    error swallow all execute (previously only stubbed away). T-357."""

    class _FakeClient:
        def __init__(self, scans, raise_for=(), scans_error=None):
            self.scans = scans
            self.raise_for = set(raise_for)
            self.scans_error = scans_error
            self.run_scan_calls: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get_popular_watchlists(self):
            return list(POPULAR)

        def get_scans(self):
            if self.scans_error is not None:
                raise self.scans_error
            return list(self.scans)

        def run_scan(self, scan_id):
            from clients.robinhood_client import RobinhoodClientError

            self.run_scan_calls.append(scan_id)
            if scan_id in self.raise_for:
                raise RobinhoodClientError(f"scan {scan_id}: HTTP 500")
            return [{"symbol": "NVDA"}]

    def _wire(self, monkeypatch, fake):
        import clients.robinhood_client as rh_mod

        monkeypatch.setattr(rh_mod, "RobinhoodClient", lambda *a, **k: fake)

    def test_id_probing_slice_and_per_scan_swallow(self, monkeypatch):
        assert crowding.MAX_SCANS_PER_RUN == 5  # the counts below assume this
        scans = [
            {"id": "s1"},
            {"scan_id": "s2"},   # fallback key must still be probed
            {"name": "no-id"},   # no id under either spelling -> skipped
            {"id": "s3"},        # run_scan raises -> swallowed, others survive
            {"id": "s4"},
            {"id": "s5-beyond-slice"},
            {"id": "s6-beyond-slice"},
        ]
        fake = self._FakeClient(scans, raise_for={"s3"})
        self._wire(monkeypatch, fake)

        popular, results = crowding.fetch_crowding()

        assert popular == POPULAR
        assert fake.run_scan_calls == ["s1", "s2", "s3", "s4"], (
            "expected the 5-scan slice minus the id-less row; scans beyond "
            "MAX_SCANS_PER_RUN must never run"
        )
        assert set(results) == {"s1", "s2", "s4"}, "the raiser is swallowed per-scan"
        assert results["s2"] == [{"symbol": "NVDA"}]

    def test_get_scans_failure_degrades_to_watchlists_only(self, monkeypatch):
        from clients.robinhood_client import RobinhoodClientError

        fake = self._FakeClient([], scans_error=RobinhoodClientError("scans down"))
        self._wire(monkeypatch, fake)

        popular, results = crowding.fetch_crowding()

        assert popular == POPULAR
        assert results == {}
        assert fake.run_scan_calls == []


def _local_import_closure(roots: list[Path]) -> set[Path]:
    """Transitive closure of the roots' imports resolved inside scripts/."""
    import ast

    seen: set[Path] = set()
    frontier = list(roots)
    while frontier:
        path = frontier.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
        for name in names:
            rel = name.replace(".", "/")
            for candidate in (_SCRIPTS_DIR / f"{rel}.py", _SCRIPTS_DIR / rel / "__init__.py"):
                if candidate.exists():
                    frontier.append(candidate)
    return seen


class TestCrowdingCannotTripGates:
    """Belt-and-suspenders pin that the gate chokepoints cannot see rh_crowding.

    Gate 1 lives in scripts/workflow/gates.py (convexity_gate), Gate 2 in
    scripts/evaluate.py (determine_edge), Gate 3 in scripts/kelly.py +
    the workflow kelly_gate.

    Honest scope: the direct grep only reads the three chokepoint files, so a
    gate importing an intermediary module that reads rh_crowding would slip
    past it — the transitive test below walks the import closure to close
    that hole. Neither proves runtime behavior (a DB query built from string
    fragments would evade both); they are tripwires, not proofs.
    """

    GATE_FILES = (
        "workflow/gates.py",
        "kelly.py",
        "evaluate.py",
    )
    FORBIDDEN = re.compile(r"robinhood|rh_crowding|popular_watchlist", re.IGNORECASE)
    # db/writer.py is the shared persistence layer: it DEFINES the
    # upsert_rh_crowding_rows write path (INSERT only, no read/select of
    # rh_crowding), so gate files importing db.writer cannot read crowding
    # through it. Nothing else in the closure may match.
    TRANSITIVE_ALLOWED = ("db/writer.py",)

    def test_gate_files_never_reference_crowding_or_robinhood(self):
        for rel in self.GATE_FILES:
            text = (_SCRIPTS_DIR / rel).read_text(encoding="utf-8")
            hit = self.FORBIDDEN.search(text)
            assert hit is None, (
                f"scripts/{rel} references {hit.group(0)!r} — the Robinhood "
                "crowding overlay must never feed the Four Gates"
            )

    def test_gate_import_closure_never_references_crowding(self):
        """A gate importing a retail_overlay.py that reads rh_crowding must
        fail here even though the direct grep above cannot see it. T-357."""
        closure = _local_import_closure(
            [_SCRIPTS_DIR / rel for rel in self.GATE_FILES]
        )
        allowed = {_SCRIPTS_DIR / rel for rel in self.TRANSITIVE_ALLOWED}
        assert len(closure) > len(self.GATE_FILES), "closure walk found no imports"
        for path in sorted(closure - allowed):
            hit = self.FORBIDDEN.search(path.read_text(encoding="utf-8"))
            assert hit is None, (
                f"{path.relative_to(_SCRIPTS_DIR)} (imported by a gate file) "
                f"references {hit.group(0)!r} — the crowding overlay must "
                "never feed the Four Gates, directly or transitively"
            )
        # The allowlist stays honest: writer must never grow a crowding read.
        writer_text = (_SCRIPTS_DIR / "db" / "writer.py").read_text(encoding="utf-8")
        assert not re.search(r"SELECT[^;]*rh_crowding", writer_text, re.IGNORECASE)

    def test_crowding_script_never_imports_the_gate_chokepoints(self):
        text = (_SCRIPTS_DIR / "fetch_rh_crowding.py").read_text(encoding="utf-8")
        for forbidden in ("workflow.gates", "from kelly", "import kelly", "evaluate"):
            assert forbidden not in text, (
                f"fetch_rh_crowding.py mentions {forbidden!r} — crowding is a "
                "descriptive overlay, not a gate input"
            )

    def test_gate_math_is_unchanged_by_crowding_extremes(self):
        """The gates take only structural/odds inputs; a maximally crowded
        symbol and an uncrowded one hand them identical arguments."""
        from workflow.gates import convexity_gate, kelly_gate

        convex = convexity_gate(max_gain=2000.0, max_loss=1000.0)
        assert convex.passed and convex.detail["ratio"] == pytest.approx(2.0)

        kelly = kelly_gate(prob_win=0.55, odds=2.0)
        assert kelly.gate == "risk"
        # No crowding parameter exists to pass — the signature is the contract.
        import inspect
        for fn in (convexity_gate, kelly_gate):
            params = set(inspect.signature(fn).parameters)
            assert not params & {"crowding", "rh_crowding", "popular_rank", "scan_hits"}
