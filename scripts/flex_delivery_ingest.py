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

from lib.flex_classify import (
    ACTIVITY,
    TRADES,
    FlexClassifyError,
    classify_flex_xml,
    statement_metadata,
)


def _sha256(xml_text: str) -> str:
    return hashlib.sha256(xml_text.encode("utf-8")).hexdigest()


def claim_flex_delivery(content_sha256: str, **kwargs: Any) -> bool:
    """Indirection so the claim is injectable in tests. See db.writer."""
    from db.writer import claim_flex_delivery as _claim  # noqa: PLC0415

    return _claim(content_sha256, **kwargs)


def release_flex_delivery(content_sha256: str) -> None:
    """Indirection so the release is injectable in tests. See db.writer."""
    from db.writer import release_flex_delivery as _release  # noqa: PLC0415

    _release(content_sha256)


def ingest_xml(xml_text: str, *, source_path: str = "") -> Dict[str, Any]:
    kind = classify_flex_xml(xml_text)
    digest = _sha256(xml_text)
    meta = statement_metadata(xml_text)
    # BEFORE any writer: re-applying a statement is not idempotent (R-329), so
    # the fingerprint has to gate the run rather than annotate it. R-326.
    if not claim_flex_delivery(
        digest,
        classified_as=kind,
        period_from=meta["period_from"],
        period_to=meta["period_to"],
        source_path=source_path or None,
    ):
        return {
            "ok": True,
            "outcome": "duplicate",
            "classified_as": kind,
            "content_sha256": digest,
            "source_path": source_path,
        }
    if kind == ACTIVITY:
        import cash_flow_sync
        import perf_twr_builder

        if not source_path:
            raise FlexClassifyError("activity ingest requires a filesystem path")
        cash_code = cash_flow_sync.main(["--from-file", source_path, "--no-file"])
        if cash_code != 0:
            # `upsert_cash_flow_rows` chunks its writes, so a failure leaves the
            # earlier chunks committed. Building the TWR series over a
            # half-written `cash_flows` and persisting it as authoritative
            # turns a partial write into a published number. R-323.
            #
            # The claim was taken before the writers, so hand it back: without
            # this the operator's re-drop of the fixed file is a "duplicate"
            # no-op and the half-written chunks are never repaired. T-257.
            release_flex_delivery(digest)
            return {
                "ok": False,
                "classified_as": kind,
                "content_sha256": digest,
                "cash_exit": cash_code,
                "twr_status": None,
                "error": f"cash_flow_sync failed (exit {cash_code}); TWR not rebuilt",
                "source_path": source_path,
            }
        twr = perf_twr_builder.build_and_persist(from_file=source_path, persist=True)
        if twr.get("status") not in ("ok", "stale"):
            release_flex_delivery(digest)
        return {
            "ok": twr.get("status") in ("ok", "stale"),
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
        if not result.get("ok"):
            release_flex_delivery(digest)
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
        # Per-file isolation: earlier files have already mutated `cash_flows`,
        # `journal` and TWR by the time a later one fails, so letting anything
        # but a classify error escape aborted the batch with nothing printed
        # and the remaining files unprocessed. R-361.
        try:
            result = ingest_path(path)
        except FlexClassifyError as exc:
            result = {"ok": False, "error": str(exc), "source_path": str(path)}
        except Exception as exc:  # noqa: BLE001 — one bad file must not abort the batch
            result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "source_path": str(path),
            }
        results.append(result)
        ok = ok and bool(result.get("ok"))
    print(json.dumps({"ok": ok, "results": results, "ran_at": datetime.now(timezone.utc).isoformat()}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
