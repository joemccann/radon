#!/usr/bin/env python3.13
"""Operator extract: Turso ``posts`` -> data/slm/tagger/v1 (never committed).

    python3.13 scripts/newsfeed/slm/extract_cli.py --out data/slm/tagger/v1

Requires TURSO_DB_URL and TURSO_AUTH_TOKEN on the Hetzner or Mini host.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.build_dataset import (  # noqa: E402
    build_dataset,
    derive_taxonomy,
    fetch_posts,
)


def _turso_execute(sql: str, args: tuple) -> list[tuple]:
    from db.hrana_http import hrana_query

    return hrana_query(sql, args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/slm/tagger/v1")
    args = parser.parse_args(argv)
    raw = fetch_posts(_turso_execute)
    kept_preview = build_dataset(raw, taxonomy=[], out_dir=None)
    taxonomy = derive_taxonomy(kept_preview["rows"])
    result = build_dataset(raw, taxonomy=taxonomy, out_dir=Path(args.out))
    json.dump(result["manifest"], sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
