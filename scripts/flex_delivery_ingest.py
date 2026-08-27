#!/usr/bin/env python3
"""Ingest a saved Flex XML from a local directory. No Flex Web Service.

Timer is not installed. Call with --file or --inbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.flex_classify import ACTIVITY, TRADES, FlexClassifyError, classify_flex_xml


def _sha256(xml_text: str) -> str:
    return hashlib.sha256(xml_text.encode("utf-8")).hexdigest()


def ingest_xml(xml_text: str, *, source_path: str = "") -> Dict[str, Any]:
    kind = classify_flex_xml(xml_text)
    digest = _sha256(xml_text)
    if kind == ACTIVITY:
        import cash_flow_sync
        import perf_twr_builder

        if not source_path:
            raise FlexClassifyError("activity ingest requires a filesystem path")
        cash_code = cash_flow_sync.main(["--from-file", source_path, "--no-file"])
        twr = perf_twr_builder.build_and_persist(from_file=source_path, persist=True)
        return {
            "ok": cash_code == 0 and twr.get("status") in ("ok", "stale"),
            "classified_as": kind,
            "content_sha256": digest,
            "cash_exit": cash_code,
            "twr_status": twr.get("status"),
            "source_path": source_path,
        }
    if kind == TRADES:
        import journal_rehydrate

        if not source_path:
            raise FlexClassifyError("trades ingest requires a filesystem path")
        result = journal_rehydrate.rehydrate(xml_text=xml_text)
        return {
            "ok": bool(result.get("ok")),
            "classified_as": kind,
            "content_sha256": digest,
            "imported": result.get("imported"),
            "skipped": result.get("skipped"),
            "error": result.get("error"),
            "source_path": source_path,
        }
    raise FlexClassifyError(kind)


def ingest_path(path: Path) -> Dict[str, Any]:
    xml_text = path.read_text(encoding="utf-8")
    return ingest_xml(xml_text, source_path=str(path))


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", metavar="PATH", help="One XML file")
    parser.add_argument("--inbox", metavar="DIR", help="Directory of *.xml (not a timer)")
    args = parser.parse_args(argv)
    if not args.file and not args.inbox:
        parser.error("pass --file or --inbox")
    paths: List[Path] = []
    if args.file:
        paths.append(Path(args.file))
    if args.inbox:
        paths.extend(sorted(Path(args.inbox).glob("*.xml")))
    results = []
    ok = True
    for path in paths:
        try:
            result = ingest_path(path)
        except FlexClassifyError as exc:
            result = {"ok": False, "error": str(exc), "source_path": str(path)}
        results.append(result)
        ok = ok and bool(result.get("ok"))
    print(json.dumps({"ok": ok, "results": results, "ran_at": datetime.now(timezone.utc).isoformat()}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
