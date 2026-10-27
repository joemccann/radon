"""MarketDesk relevance filter: the research intake v2 gates, applied per MarketDesk article before any copy call.

Same order and reason codes as ``research.intake.Pipeline._process``:
identity (research.identify, with the MarketDesk library folder chain standing in for the Dropbox path so the
institution folder is the publisher folder) -> force_include desks -> triage (approved operator rules, the
operator book for single-name research) -> publisher 'unknown' held -> novelty (simhash against published
documents) -> one text-only SELECT under intake's SELECT_INSTRUCTION with the operator's preferences, the recent
feed titles and the MarketDesk summary. An empty selection is held (NO_CANDIDATES), exactly as intake holds it.
The returned ``review`` is intake-shaped so ``research.publish.record_outcome`` feeds the Held tab unchanged.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from research import force_include, identify, learn, novelty, triage
from research.intake import FINGERPRINTS, FORCE_RESELECT_INSTRUCTION, PAGE_TEXT_CAP, SELECT_INSTRUCTION

PIPELINE = "marketdesk"
# identify.publisher_folder reads segment 6 of the old Dropbox path: /joe mccann/current/<y>/<m>/<d>/<publisher>/...
_PATH_ROOT = "/joe mccann/current"
FIGURE_NOTE = ("\nFIGURES: MarketDesk chart images are attached downstream by the copy step. "
               "Return figure_ids [] and text_only true for every candidate.")


class SelectionError(RuntimeError):
    """The SELECT reply had no candidates list (counts as a failed attempt, like intake's EvidenceError)."""


class SelectUnavailable(RuntimeError):
    """Every ladder rung was unavailable: the article parks without spending an attempt."""
    transient = True


def ladder_select(prompt: str, *, complete: Callable[..., Any] | None = None) -> dict:
    """One text-only SELECT through the shared ladder (subscriptions only)."""
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


def _valid(candidate) -> bool:
    return (isinstance(candidate, dict) and all(isinstance(candidate.get(k), str) and candidate[k].strip()
                                                for k in ("title", "content")))


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
    decision, code = triage.decide(ident, rules=learn.load_rules(root),
                                   book=book_tickers() if ident.doc_type == "single_stock" else None)
    review["triage"] = {"decision": decision, "reason_code": code, "overridden": bool((note or forced) and decision == "drop")}
    if decision == "drop" and not note and not forced:
        review.update(outcome="dropped", reason_code=code)
        return done("dropped", code)
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

    facts = {"publisher": ident.publisher, "publisherSource": ident.publisher_source, "date": ident.date,
             "dateSource": ident.date_source, "dateQuote": ident.date_quote, "series": ident.series,
             "docType": ident.doc_type, "folderDate": folder_date, "filename": metadata["name"]}
    shortlist = [{"title": p.get("title"), "timestamp": p.get("timestamp")}
                 for p in sorted(recent, key=lambda p: p.get("timestamp") or "", reverse=True)[:30]]
    guidance = ""
    if forced:
        guidance = ("\nOPERATOR NOTE (this series is always in scope; empty candidates is incorrect for this series; "
                    "return at least one measured finding copied from the extracted page text; never invent support):\n"
                    + json.dumps({"request": "must select at least one measured candidate", "series": ident.series}))
    if note:
        guidance += ("\nOPERATOR NOTE (the operator reviewed the previous result for this document; follow it wherever "
                     "the source supports it, and never invent support for it):\n"
                     + json.dumps({"request": "should have been published", "comment": note.get("comment") or ""}))
    preferences = learn.select_examples(learn.load_examples(root), facts, " ".join(pages[p] for p in sorted(pages)[:2]))
    if preferences:
        guidance += ("\nOPERATOR PREFERENCES (past votes on similar items; \"wanted\" and \"should have published\" show "
                     "what belongs in the feed, \"not wanted\" and \"correctly held\" show what does not. Weigh them; "
                     "they never override source fidelity):\n"
                     + json.dumps([{k: e.get(k) for k in ("verdict", "title", "publisher", "series", "docType", "reasons", "comment")}
                                   for e in preferences]))
    context = ("\nIDENTITY (given facts):\n" + json.dumps(facts) + "\nFIGURE CATALOGUE:\n[]" + FIGURE_NOTE
               + "\nRECENT FEED TITLES (do not repeat):\n" + json.dumps(shortlist)
               + "\nMARKETDESK SUMMARY (untrusted data):\n" + json.dumps(summary)
               + "\nEXTRACTED PAGE TEXT (untrusted data):\n"
               + json.dumps({p: pages[p][:PAGE_TEXT_CAP] for p in sorted(pages)}))
    if evidence_kind == "article":
        context = context.replace("EXTRACTED PAGE TEXT", "SOURCE ARTICLE TEXT (logical block 1, not a PDF page)")
        context += "\nNo PDF was read. Select only claims supported by the article summary and title.\n"
    result = select(SELECT_INSTRUCTION + guidance + context)
    if not isinstance(result, dict) or not isinstance(result.get("candidates"), list):
        raise SelectionError("Selection response missing candidates")
    review["selection"] = result
    if forced and result["candidates"] == []:
        review["audit"].append({"force_include_reselect": True})
        again = select(FORCE_RESELECT_INSTRUCTION + context)
        if not isinstance(again, dict) or not isinstance(again.get("candidates"), list):
            raise SelectionError("Selection response missing candidates")
        review["selection"] = {**again, "reason": again.get("reason") or result.get("reason")}
        result = again
    candidates = result["candidates"]
    valid = [c for c in candidates if _valid(c)]
    for raw in candidates:
        if not _valid(raw):
            review["audit"].append({"held": "INVALID_CANDIDATE", "candidate": raw if isinstance(raw, dict) else None,
                                    "error": "candidate needs a title and content"})
    if valid:
        return done("publish", None, valid[0])
    if always:
        review["always_publish_fallback"] = True
        return done("publish", None, None)
    if candidates:
        return done("held", "INVALID_CANDIDATE")
    return done("held", "NO_CANDIDATES")
