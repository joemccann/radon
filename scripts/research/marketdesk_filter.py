"""MarketDesk admission: source identity, usable facts and published-content deduplication.

The operator's all-suitable-reports policy admits FX and off-book single-name
research. Neither Dropbox triage rules nor a model relevance verdict can veto
MarketDesk copy generation. The downstream copy validator and final Humanizer
certificate still require source-grounded prose. Dropbox intake is unchanged.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from research import force_include, identify, novelty
from research.intake import FINGERPRINTS

PIPELINE = "marketdesk"
# identify.publisher_folder reads segment 6 of the old Dropbox path: /joe mccann/current/<y>/<m>/<d>/<publisher>/...
_PATH_ROOT = "/joe mccann/current"


class SelectionError(RuntimeError):
    """The SELECT reply had no candidates list (counts as a failed attempt, like intake's EvidenceError)."""


class SelectUnavailable(RuntimeError):
    """Every ladder rung was unavailable: the article parks without spending an attempt."""
    transient = True


def ladder_select(prompt: str, *, complete: Callable[..., Any] | None = None) -> dict:
    """Legacy extraction helper; never called by all-suitable MarketDesk admission."""
    from clients.model_ladder import ModelLadderExhausted, ModelResponseError, complete_text_json, safe_error_message
    complete = complete or complete_text_json

    def accept(obj) -> bool:
        return isinstance(obj, dict) and isinstance(obj.get("candidates"), list)

    try:
        result = complete(prompt, max_tokens=4000, read_timeout=120.0, require_end_turn=True, accept=accept,
                          log_prefix="marketdesk-select")
    except ModelLadderExhausted as exc:
        raise SelectUnavailable(safe_error_message(exc)) from None
    except ModelResponseError as exc:
        raise SelectionError(safe_error_message(exc)) from None
    return result.data


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
           summary: list[str], root, select: Callable[[str], Any], book_tickers: Callable[[], Any],
           recent: list[dict], index: dict, note: dict | None = None, evidence_kind: str = "pdf") -> Decision:
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
    review["admission_policy"] = "all_suitable_reports"
    if not text.strip() or not any(isinstance(item, str) and item.strip() for item in summary):
        review["audit"].append({"held": "SOURCE_MATERIAL_MISSING"})
        return done("held", "SOURCE_MATERIAL_MISSING")
    if ident.publisher == "unknown":
        review["audit"].append({"held": "PUBLISHER_UNKNOWN"})
        return done("held", "PUBLISHER_UNKNOWN")

    others = {k: v for k, v in index.items() if k != "marketdesk:" + article_id}
    duplicate = novelty.duplicate_of(fp, {k: v[0] for k, v in others.items()})
    review["novelty"] = {"fingerprint": fp, "duplicate_of": duplicate}
    other_pub = others[duplicate][1] if duplicate else None
    if duplicate and not (always and other_pub and other_pub != ident.publisher):
        review.update(outcome="dropped", reason_code="DUPLICATE_OF_PUBLISHED")
        return done("dropped", "DUPLICATE_OF_PUBLISHED")

    # Copy generation sees the original source summary and images directly.
    # No preliminary model can exclude an otherwise suitable report by topic,
    # portfolio membership, routine-content labels, or an empty SELECT reply.
    return done("publish")
