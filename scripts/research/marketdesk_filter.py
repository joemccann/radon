"""MarketDesk admission: primary issuer eligibility, usable sources and deduplication.

Broad market research remains eligible without a model relevance veto. Company
research requires every primary issuer in the current portfolio or watchlist.
Source grounding and the final Humanizer remain downstream hard gates.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from research import equity_admission, force_include, identify, novelty
from research.intake import FINGERPRINTS

PIPELINE = "marketdesk"
# identify.publisher_folder reads segment 6 of the old Dropbox path: /joe mccann/current/<y>/<m>/<d>/<publisher>/...
_PATH_ROOT = "/joe mccann/current"


@dataclass
class Decision:
    outcome: str                 # publish | held | dropped
    reason_code: str | None
    review: dict
    candidate: dict | None
    fingerprint: int
    identity: Any


def _metadata(article_id: str, title: str, folders: list[str]) -> dict:
    name = (title or article_id).strip() + ".pdf"
    chain = [f.strip().lower() for f in folders if isinstance(f, str) and f.strip()]
    return {"id": article_id, "name": name, "path_lower": "/".join([_PATH_ROOT, *chain, name.lower()])}


def fingerprint_index(root, ledger_rows) -> dict:
    """{key: (fingerprint, publisher)}: the old pipeline's published documents plus published MarketDesk rows."""
    index: dict = {}
    try:
        stored = json.loads((Path(root) / FINGERPRINTS).read_text())
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    publishers = stored.get("publishers") if isinstance(stored.get("publishers"), dict) else {}
    for key, value in (stored.get("documents") or {}).items() if isinstance(stored.get("documents"), dict) else ():
        try:
            index[key] = (int(value, 16), publishers.get(key))
        except (TypeError, ValueError):
            continue
    for article_id, fp, publisher in ledger_rows:
        try:
            index["marketdesk:" + article_id] = (int(fp, 16), publisher)
        except (TypeError, ValueError):
            continue
    return index


def decide(*, article_id: str, title: str, folders: list[str], folder_date: str, pages: dict, created,
           summary: list[str], root, book_tickers: Callable[[], Any], index: dict,
           note: dict | None = None) -> Decision:
    root = Path(root)
    note = note if isinstance(note, dict) else {}
    metadata = _metadata(article_id, title, folders)
    ident = identify.identify(pages, metadata, folder_date, pdf_created=created)
    page_one = pages.get(1, "")
    text = " ".join(pages[p] for p in sorted(pages))
    review: dict = {"pipeline": PIPELINE, "identity": ident.as_dict(), "audit": [], "posts": [],
                    "document": {"page_count": len(pages), "excerpt": re.sub(r"\s+", " ", page_one).strip()[:900]}}
    fp = novelty.fingerprint(text)

    def done(outcome, code=None, candidate=None):
        return Decision(outcome, code, review, candidate, fp, ident)

    desk = force_include.match(ident, filename=metadata["name"], page_text=page_one)
    forced = desk is not None
    always = bool(desk and desk.always_publish)
    review.update(force_include=forced, always_publish=always, operator_note=note or None)
    review["admission_policy"] = "current_book_issuer_research"
    if not text.strip() or not any(isinstance(item, str) and item.strip() for item in summary):
        review["audit"].append({"held": "SOURCE_MATERIAL_MISSING"})
        return done("held", "SOURCE_MATERIAL_MISSING")
    if ident.publisher == "unknown":
        review["audit"].append({"held": "PUBLISHER_UNKNOWN"})
        return done("held", "PUBLISHER_UNKNOWN")

    eligibility, reason, subjects = equity_admission.decide(
        metadata["name"], text, doc_type=ident.doc_type, book_tickers=book_tickers)
    review["equity_admission"] = {"outcome": eligibility, "reason_code": reason, "subjects": list(subjects)}
    if eligibility != "publish":
        review["audit"].append({eligibility: reason})
        return done(eligibility, reason)

    others = {k: v for k, v in index.items() if k != "marketdesk:" + article_id}
    duplicate = novelty.duplicate_of(fp, {k: v[0] for k, v in others.items()})
    review["novelty"] = {"fingerprint": fp, "duplicate_of": duplicate}
    other_pub = others[duplicate][1] if duplicate else None
    if duplicate and not (always and other_pub and other_pub != ident.publisher):
        review.update(outcome="dropped", reason_code="DUPLICATE_OF_PUBLISHED")
        return done("dropped", "DUPLICATE_OF_PUBLISHED")

    # Copy generation sees the original source summary and images directly.
    # No preliminary model relevance verdict can exclude eligible research.
    return done("publish")
