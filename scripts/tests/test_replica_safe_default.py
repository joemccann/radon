"""Static guards: no DB client can open the libsql embedded replica.

The replica was retired 2026-05-20. DUR-07 made it opt-in via
RADON_DB_USE_REPLICA=1; that opt-in is now removed, so these tests pin, at
the source level, that:

  * none of the three DB clients builds a replica connection (no
    ``sync_url`` / ``syncUrl`` and no ``replica.db`` path)
  * no production source reads RADON_DB_USE_REPLICA at all
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DB_CLIENTS = ("scripts/db/client.py", "scripts/db/writer.js", "web/lib/db.ts")
REPLICA_MARKERS = ("sync_url", "syncUrl", "replica.db", "RADON_DB_USE_REPLICA")

PRODUCTION_SOURCE_DIRS = ("scripts", "web/lib", "web/app", "web/components")
SOURCE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".mjs", ".sh"}
EXCLUDED_PARTS = {"tests", "node_modules", ".next", "__pycache__", "e2e"}


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


class TestReplicaCannotBeOpened:
    @pytest.mark.parametrize("path", DB_CLIENTS)
    def test_client_has_no_replica_branch(self, path: str) -> None:
        source = _read(path)
        found = [marker for marker in REPLICA_MARKERS if marker in source]
        assert not found, (
            f"{path} still carries an embedded-replica branch ({found}). The "
            f"replica was retired 2026-05-20; any switch that reopens it "
            f"resurrects data/replica.db (the ib-watchdog 6h hang, 2026-06-10)."
        )

    def test_no_production_source_reads_the_opt_in(self) -> None:
        offenders = []
        for base in PRODUCTION_SOURCE_DIRS:
            for path in (REPO_ROOT / base).rglob("*"):
                if path.suffix not in SOURCE_SUFFIXES:
                    continue
                if EXCLUDED_PARTS.intersection(path.parts):
                    continue
                if "RADON_DB_USE_REPLICA" in path.read_text(
                    encoding="utf-8", errors="ignore"
                ):
                    offenders.append(path.relative_to(REPO_ROOT).as_posix())
        assert not offenders, (
            f"RADON_DB_USE_REPLICA referenced in production source: "
            f"{offenders}. The opt-in was removed; nothing may read it."
        )
