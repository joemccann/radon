"""Static guards: the libsql embedded replica must stay opt-in ONLY.

Inversion of the retired test_no_replica_env_timing.py. That file policed
~17 per-entrypoint RADON_DB_NO_REPLICA setdefaults because the DB clients
defaulted to the replica; since DUR-07 the default is direct-to-cloud in
the clients themselves, so the scattered setdefaults are gone and these
tests pin the safe default at the source level instead:

  * all three DB clients gate the replica on an explicit
    RADON_DB_USE_REPLICA=1 opt-in
  * no production source ever sets RADON_DB_USE_REPLICA — opting in is an
    operator decision (env / unit file), never code
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DB_CLIENTS = {
    "scripts/db/client.py": 'os.environ.get("RADON_DB_USE_REPLICA") == "1"',
    "scripts/db/writer.js": 'process.env.RADON_DB_USE_REPLICA === "1"',
    "web/lib/db.ts": 'process.env.RADON_DB_USE_REPLICA === "1"',
}

# scripts/db/writer.py only documents the precedence in a docstring.
ALLOWED_MENTIONS = set(DB_CLIENTS) | {"scripts/db/writer.py"}

PRODUCTION_SOURCE_DIRS = ("scripts", "web/lib", "web/app", "web/components")
SOURCE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".mjs", ".sh"}
EXCLUDED_PARTS = {"tests", "node_modules", ".next", "__pycache__", "e2e"}


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


class TestReplicaIsOptInOnly:
    @pytest.mark.parametrize("path,opt_in_gate", sorted(DB_CLIENTS.items()))
    def test_client_gates_replica_on_explicit_opt_in(
        self, path: str, opt_in_gate: str
    ) -> None:
        source = _read(path)
        assert opt_in_gate in source, (
            f"{path} must take the embedded-replica branch ONLY behind an "
            f"explicit RADON_DB_USE_REPLICA=1 opt-in ({opt_in_gate!r}). The "
            f"replica was retired 2026-05-20; an unconditional or "
            f"NO_REPLICA-only gate resurrects it on any entrypoint that "
            f"forgets an env var (the ib-watchdog 6h hang, 2026-06-10)."
        )

    @pytest.mark.parametrize("path", sorted(DB_CLIENTS))
    def test_legacy_kill_switch_still_consulted(self, path: str) -> None:
        assert "RADON_DB_NO_REPLICA" in _read(path), (
            f"{path} must keep honouring the legacy RADON_DB_NO_REPLICA "
            f"kill switch (fleet units still set it as belt-and-suspenders)."
        )

    def test_no_production_source_opts_in(self) -> None:
        offenders = []
        for base in PRODUCTION_SOURCE_DIRS:
            for path in (REPO_ROOT / base).rglob("*"):
                if path.suffix not in SOURCE_SUFFIXES:
                    continue
                if EXCLUDED_PARTS.intersection(path.parts):
                    continue
                rel = path.relative_to(REPO_ROOT).as_posix()
                if rel in ALLOWED_MENTIONS:
                    continue
                if "RADON_DB_USE_REPLICA" in path.read_text(
                    encoding="utf-8", errors="ignore"
                ):
                    offenders.append(rel)
        assert not offenders, (
            f"RADON_DB_USE_REPLICA referenced outside the DB clients: "
            f"{offenders}. Opting back into the retired replica is an "
            f"operator/env decision, never a code default."
        )
