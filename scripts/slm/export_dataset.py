"""Export the SLM fine-tune corpus from Turso as chat-format JSONL.

Step 1 of the Radon SLM path. Two tasks, each a distillation of what a
frontier model already produced and Radon already accepted:

  tagger   posts.tags_text (text-tagger output) under the exact system /
           user prompts scripts/newsfeed/tagger.js sends today.
  distill  knowledge.summary + metadata.tickers under the exact prompt
           scripts/knowledge/distill.py sends today (egress-scrubbed).

Rows that would not pass the live acceptance rules (exactly 3 normalised
tags; non-empty summary) are dropped so the model never learns a rejected
answer. Split is a deterministic hash on the example id so re-exports keep
the same hold-out set.

Usage: python scripts/slm/export_dataset.py [--out data/slm] [--holdout 0.1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.distill import (  # noqa: E402
    MAX_CONTENT_CHARS,
    _SYSTEM_PROMPT as DISTILL_SYSTEM_PROMPT,
    _scrub_for_egress,
)

TASKS = ("tagger", "distill")
TAGGER_BODY_CHARS = 1500  # scripts/newsfeed/tagger.js buildUserPrompt

# ── tagger prompts: byte-for-byte scripts/newsfeed/tagger.js ──────────────

_TAGGER_SYSTEM_LINES = [
    "You are a financial-news tagger for an institutional trading dashboard.",
    "",
    "Pick EXACTLY 3 tags that best capture the post's core themes.",
    "",
    "Priority order — apply each step and stop only when you have 3 tags:",
    "  1. TECHNICAL SIGNAL named explicitly — when the post calls out a candlestick pattern, indicator, chart pattern, or price-action concept, tag the SPECIFIC name. These are high-information signals; do not skip them.",
    "       Candlestick patterns: SHOOTING-STAR, HAMMER, INVERSE-HAMMER, HANGING-MAN, DOJI, ENGULFING, MORNING-STAR, EVENING-STAR, HARAMI, MARUBOZU, PIERCING-LINE, DARK-CLOUD-COVER, THREE-WHITE-SOLDIERS, THREE-BLACK-CROWS.",
    "       Chart patterns: HEAD-SHOULDERS, INVERSE-HEAD-SHOULDERS, DOUBLE-TOP, DOUBLE-BOTTOM, TRIPLE-TOP, TRIPLE-BOTTOM, TRIANGLE, ASCENDING-TRIANGLE, DESCENDING-TRIANGLE, FLAG, PENNANT, WEDGE, CUP-AND-HANDLE, BREAKOUT, BREAKDOWN, GAP, ISLAND-REVERSAL.",
    "       Indicators: RSI, MACD, MOVING-AVERAGE, GOLDEN-CROSS, DEATH-CROSS, BOLLINGER-BANDS, STOCHASTIC, ADX, ICHIMOKU, FIBONACCI, VWAP, OBV, ATR, PARABOLIC-SAR, KELTNER-CHANNEL.",
    "       Price-action: SUPPORT, RESISTANCE, TRENDLINE, OVERSOLD, OVERBOUGHT, DIVERGENCE, ELLIOTT-WAVE.",
    "       (Note: MOMENTUM, TREND, RANGE, PIVOT, MEAN-REVERSION are also legitimate tags — pick them when the post calls them out — but treat them as factor/macro concepts, NOT specifically TA. Tag MOMENTUM for momentum-factor / MoMo-basket posts; tag TREND for CTA/trend-following macro posts.)",
    "       Use the umbrella TECHNICAL-ANALYSIS only when the post discusses TA generically without naming a specific pattern/indicator.",
    "  2. INSTRUMENT or PRODUCT named in the post (puts, calls, options, BTC, oil, gold, futures, swaps, ETFs, bonds, SPX, SPY).",
    "  3. SECTOR or asset class focus (semis, energy, banks, credit, crypto, equities).",
    "  4. THEME or narrative (positioning, hedging, macro, Fed, inflation, earnings, geopolitics).",
    "",
    "Reuse an existing tag when one fits; coin a NEW tag only when nothing in the existing set captures the concept. Do not split a single concept across multiple near-synonyms.",
    "",
    "Naming rules — apply STRICTLY so tags merge cleanly across posts:",
    "  - ALL TAGS ARE UPPERCASE. No exceptions. Examples: BTC, OIL, VOL, PUTS, OPTIONS, POSITIONING, FED, RSI.",
    "  - Multi-word concepts use UPPERCASE kebab-case: PUT-CALL-RATIO, FUND-FLOWS, SINGLE-STOCK-VOL, DEALER-GAMMA, TAIL-HEDGE, SHOOTING-STAR, HEAD-SHOULDERS.",
    "  - Allowed characters: A-Z, 0-9, hyphen, ampersand. No spaces, no lowercase, no underscores.",
    "",
    "Disambiguation:",
    "  - VOL vs VIX: VIX only when the VIX index is explicitly named or charted; otherwise VOL.",
    "  - PUTS vs VOL: if the post is specifically about puts / put-call ratio / put protection, tag PUTS (not VOL).",
    "  - HEDGING is the action; PUTS/CALLS/OPTIONS are instruments — tag both when relevant.",
    "  - SKEW is options skew specifically.",
    "  - GAMMA is dealer-gamma / GEX.",
    "  - POSITIONING is who is long/short and how exposed.",
    "  - TECHNICAL SIGNALS: prefer the specific named pattern/indicator (SHOOTING-STAR, RSI, HEAD-SHOULDERS) over generic TECHNICAL-ANALYSIS. A post that names two TA concepts (e.g. shooting star AND inverse hammer) should tag both when slot count allows.",
    "  - CANDLESTICK is the umbrella; only use it when the post discusses candlestick analysis without naming a specific pattern.",
    "",
    'Output FORMAT: STRICT JSON. {"tags": ["...","...","..."]}. Exactly 3. No prose.',
    "",
]


def tagger_system_prompt(taxonomy: list[str]) -> str:
    existing = ", ".join(taxonomy) if taxonomy else "(none yet)"
    return "\n".join(_TAGGER_SYSTEM_LINES + [f"Existing tags (reuse when possible): {existing}"])


def tagger_user_prompt(title: str | None, content: str | None) -> str:
    return f"Title: {title or ''}\nBody: {(content or '')[:TAGGER_BODY_CHARS]}"


# ── tag normaliser: mirrors normaliseSingleTag / normaliseTags in tagger.js ─

def _normalise_single_tag(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    tag = raw.strip()
    if not tag:
        return None
    tag = re.sub(r"^[#\"'`(\[]+|[\.,!?:;\"'`)\]]+$", "", tag)
    if not tag:
        return None
    tag = tag.upper()
    tag = re.sub(r"[\s_]+", "-", tag)
    tag = re.sub(r"[^A-Z0-9\-&]", "", tag)
    tag = re.sub(r"-+", "-", tag).strip("-")
    return tag or None


def normalise_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        tag = _normalise_single_tag(item)
        if tag and tag not in out:
            out.append(tag)
    return out


# ── builders ───────────────────────────────────────────────────────────────

def _json_list(raw: Any) -> list | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, list) else None


def _example(task: str, ex_id: str, system: str, user: str, answer: dict) -> dict:
    return {
        "task": task,
        "id": ex_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(answer, separators=(",", ":"))},
        ],
    }


def read_taxonomy(db: Any) -> list[str]:
    rows = db.execute("SELECT tag FROM tag_taxonomy ORDER BY tag COLLATE NOCASE").fetchall()
    return normalise_tags([r[0] for r in rows])


def build_tagger_examples(db: Any) -> list[dict]:
    system = tagger_system_prompt(read_taxonomy(db))
    rows = db.execute(
        "SELECT id, title, content, images, tags, tags_text FROM posts ORDER BY timestamp, id"
    ).fetchall()
    out: list[dict] = []
    for pid, title, content, images, tags, tags_text in rows:
        answer = _json_list(tags_text)
        if answer is None:
            # Merged tags only stand in for the text tagger when no vision
            # tagger could have contributed.
            if _json_list(images):
                continue
            answer = _json_list(tags)
        normalised = normalise_tags(answer)
        if len(normalised) != 3 or len(answer or []) != 3:
            continue
        out.append(_example("tagger", pid, system, tagger_user_prompt(title, content), {"tags": normalised}))
    return out


def build_distill_examples(db: Any) -> list[dict]:
    rows = db.execute(
        "SELECT source, doc_key, chunk_ix, title, summary, content, metadata FROM knowledge"
        " WHERE summary IS NOT NULL AND TRIM(summary) <> '' ORDER BY source, doc_key, chunk_ix"
    ).fetchall()
    out: list[dict] = []
    for source, doc_key, chunk_ix, title, summary, content, metadata in rows:
        document = f"Title: {title}\n\n{content}" if title else content
        user = _scrub_for_egress(document[:MAX_CONTENT_CHARS])
        meta = {}
        try:
            meta = json.loads(metadata) if isinstance(metadata, str) else (metadata or {})
        except json.JSONDecodeError:
            meta = {}
        tickers = meta.get("tickers") if isinstance(meta, dict) else None
        tickers = [t for t in tickers if isinstance(t, str)] if isinstance(tickers, list) else []
        answer = {"summary": summary.strip(), "tickers": tickers}
        out.append(_example("distill", f"{source}:{doc_key}:{chunk_ix}", DISTILL_SYSTEM_PROMPT, user, answer))
    return out


# ── split + write ──────────────────────────────────────────────────────────

def split_examples(examples: Iterable[dict], holdout: float = 0.1) -> tuple[list[dict], list[dict]]:
    train: list[dict] = []
    evals: list[dict] = []
    for ex in examples:
        digest = hashlib.sha256(f"{ex['task']}:{ex['id']}".encode()).digest()
        bucket = int.from_bytes(digest[:4], "big") / 2**32
        (evals if bucket < holdout else train).append(ex)
    return train, evals


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def export(db: Any, out_dir: Path, holdout: float = 0.1) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    builders = {"tagger": build_tagger_examples, "distill": build_distill_examples}
    manifest: dict[str, Any] = {"holdout": holdout, "tasks": {}}
    for task in TASKS:
        train, evals = split_examples(builders[task](db), holdout)
        _write_jsonl(out_dir / f"{task}.train.jsonl", train)
        _write_jsonl(out_dir / f"{task}.eval.jsonl", evals)
        manifest["tasks"][task] = {"train": len(train), "eval": len(evals)}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(_SCRIPTS_DIR.parent / "data" / "slm"))
    parser.add_argument("--holdout", type=float, default=0.1)
    args = parser.parse_args(argv)
    from db.client import get_db

    manifest = export(get_db(), Path(args.out), args.holdout)
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
