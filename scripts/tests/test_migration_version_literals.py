"""T-373 — the version literal a migration stamps must match its filename.

0064_dispersion_source.sql and 0065_assistant_turns_provenance.sql both
shipped `INSERT OR IGNORE INTO schema_migrations … VALUES (63, …)` — a
copy-paste of 0063's literal. Harmless only because migrate.py records the
version itself after the statement loop, but the wrong literal is a landmine:
any runner that trusts the file's own INSERT (or a human replaying a single
file by hand) would mark the wrong version applied. Pin literal == filename
for every migration that carries its own INSERT.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "db" / "migrations"

_INSERT_RE = re.compile(
    r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+schema_migrations\b[^;]*?"
    r"VALUES\s*\(\s*(\d+)",
    re.I | re.S,
)


def _migration_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    assert files, f"no migrations found under {MIGRATIONS_DIR}"
    return files


def test_version_literal_matches_filename():
    mismatches = []
    stamped = 0
    for path in _migration_files():
        expected = int(path.name[:4])
        for match in _INSERT_RE.finditer(path.read_text()):
            stamped += 1
            literal = int(match.group(1))
            if literal != expected:
                mismatches.append(f"{path.name}: stamps VALUES ({literal}), expected {expected}")
    # Regex-rot guard: most migrations carry their own INSERT; if the pattern
    # ever stops matching, fail loudly instead of going vacuously green.
    assert stamped >= 40, f"only {stamped} schema_migrations INSERTs matched — regex rot?"
    assert not mismatches, "wrong version literal:\n" + "\n".join(mismatches)
