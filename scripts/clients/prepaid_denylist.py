"""Prepaid console keys that Radon never reads or sends (subscriptions only).

Operator policy 2026-10-26: Anthropic, OpenAI/Codex, xAI/Grok and Antigravity
run only on consumer subscriptions. The variable names live in
``prepaid_denylist.json`` (shared with ``scripts/newsfeed/vision_tagger.js``)
so that no auth code names them. A set variable is ignored; ``warn_ignored``
writes one stderr line naming it, never its value, at most once per process.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Mapping

IGNORED_PREPAID_ENV: tuple[str, ...] = tuple(
    json.loads((Path(__file__).with_name("prepaid_denylist.json")).read_text(encoding="utf-8"))
)

_warned: set[str] = set()


def warn_ignored(env: Mapping[str, str]) -> None:
    """One stderr warning per set deny-listed variable per process."""
    for name in IGNORED_PREPAID_ENV:
        if name in _warned or not (env.get(name) or "").strip():
            continue
        _warned.add(name)
        print(
            f"[radon] ignoring {name}: prepaid keys are never used (subscriptions only)",
            file=sys.stderr,
        )
