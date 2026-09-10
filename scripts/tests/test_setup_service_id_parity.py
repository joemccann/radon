"""R-622: the web setup route's offline service-id allowlist mirrors the
Python credential registry, and drift fails here rather than in production.

When FastAPI is unreachable the route cannot ask for the registry, but it
still has to write .env or first-run setup cannot complete. The id check
therefore falls back to a static mirror; this test is what keeps it honest.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import credentials_registry  # noqa: E402

ROUTE = SCRIPTS.parent / "web" / "app" / "api" / "setup" / "complete" / "route.ts"


def _mirror_ids() -> set[str]:
    src = ROUTE.read_text(encoding="utf-8")
    start = src.index("const KNOWN_SERVICE_IDS = new Set([")
    end = src.index("]);", start)
    return set(re.findall(r'"([a-z0-9_]+)"', src[start:end]))


def test_the_mirror_matches_the_registry_exactly():
    assert _mirror_ids() == {s.id for s in credentials_registry.SERVICES}


def test_the_offline_branch_consults_the_mirror():
    src = ROUTE.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("*")
    )
    assert "KNOWN_SERVICE_IDS.has(service)" in body
