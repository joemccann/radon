"""Canonical daily Dropbox PDF cut from research_outcomes.

Read-only. Buckets each row into published, held, hold_expired, or dropped.
HELD_EXPIRED is "hold expired (24h TTL)", never "retries exhausted".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HOLD_EXPIRED_LABEL = "hold expired (24h TTL)"
HELD_LABEL = "held (still held)"
PUBLISHED_LABEL = "published"
DROPPED_LABEL = "dropped"
BUCKET_LABELS = {
    "published": PUBLISHED_LABEL,
    "held": HELD_LABEL,
    "hold_expired": HOLD_EXPIRED_LABEL,
    "dropped": DROPPED_LABEL,
}
BUCKETS = tuple(BUCKET_LABELS)

DAY_ASSIGNMENT_RULE = (
    "Day assignment: published, held, and dropped rows use decision time "
    "(updated_at). Hold-expired rows use expired_at (fallback updated_at for "
    "legacy rows). Hold-expired rows are also listed under the day they were "
    "originally reviewed or held when held_at is known."
)

# Keep in lockstep with web/lib/researchReasonCodes.ts LABELS.
REASON_CODE_LABELS = {
    "NUMBER_NOT_ON_PAGE": "Number not on the cited page",
    "VERIFY_FAILED": "Verification failed",
    "INVALID_CANDIDATE": "Draft failed validation",
    "TEXT_ONLY_WITH_FIGURES": "Text-only draft while the cited pages have charts",
    "NO_CANDIDATES": "Nothing selected",
    "DUPLICATE_OF_PUBLISHED": "Duplicate of a published document",
    "SERIES_DENYLIST": "Series is on the denylist",
    "SINGLE_STOCK_NOT_IN_BOOK": "Single-name equity research outside the watchlist and portfolio",
    "DOC_TYPE_FX_PAIR_NOTE": "Document type: FX pair note",
    "DOC_TYPE_CALENDAR": "Document type: calendar",
    "HELD_EXPIRED": "Hold expired",
}

CUT_SQL = """SELECT outcome, folder_date, file_name, publisher, series, COALESCE(posts, 0) AS posts,
       reason_codes, work_key, updated_at, document_date, held_at, expired_at
FROM research_outcomes
WHERE (datetime(replace(updated_at, 'Z', '')) >= datetime(?) AND datetime(replace(updated_at, 'Z', '')) < datetime(?))
   OR (expired_at IS NOT NULL AND datetime(replace(expired_at, 'Z', '')) >= datetime(?) AND datetime(replace(expired_at, 'Z', '')) < datetime(?))
   OR (held_at IS NOT NULL AND datetime(replace(held_at, 'Z', '')) >= datetime(?) AND datetime(replace(held_at, 'Z', '')) < datetime(?))"""

CUT_SQL_LEGACY = """SELECT outcome, folder_date, file_name, publisher, series, COALESCE(posts, 0) AS posts,
       reason_codes, work_key, updated_at, document_date
FROM research_outcomes
WHERE datetime(replace(updated_at, 'Z', '')) >= datetime(?)
  AND datetime(replace(updated_at, 'Z', '')) < datetime(?)"""


def reason_code_label(code: str) -> str:
    if code in REASON_CODE_LABELS:
        return REASON_CODE_LABELS[code]
    words = code.lower().split("_")
    words = [w for w in words if w]
    if not words:
        return "Unknown reason"
    return words[0].capitalize() + (" " + " ".join(words[1:]) if len(words) > 1 else "")


def parse_reason_codes(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) or item is not None]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def bucket_for(row: dict) -> str:
    codes = parse_reason_codes(row.get("reason_codes"))
    if "HELD_EXPIRED" in codes:
        return "hold_expired"
    outcome = row.get("outcome")
    if outcome == "published":
        return "published"
    if outcome == "held":
        return "held"
    return "dropped"


def default_cut_date(now=None) -> str:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return (instant.astimezone(PT) - timedelta(days=1)).date().isoformat()


def pt_date(stamp) -> str | None:
    if not stamp:
        return None
    text = str(stamp).replace("Z", "+00:00")
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(PT).date().isoformat()


def primary_day(row: dict) -> str | None:
    if bucket_for(row) == "hold_expired":
        return pt_date(row.get("expired_at") or row.get("updated_at"))
    return pt_date(row.get("updated_at"))


def reviewed_day(row: dict) -> str | None:
    return pt_date(row.get("held_at"))


def _empty_bucket():
    return {"docs": 0, "posts": 0}


def _folder_slot():
    return {name: _empty_bucket() for name in BUCKETS}


def build_report(rows: list[dict], date: str) -> dict:
    if not DATE_RE.fullmatch(date):
        raise ValueError("cut date must be YYYY-MM-DD")
    buckets = {
        name: {
            "docs": 0,
            "posts": 0,
            "label": BUCKET_LABELS[name],
            **({"verdicts": Counter()} if name == "hold_expired" else {}),
            **({"reasons": Counter()} if name == "dropped" else {}),
        }
        for name in BUCKETS
    }
    by_folder: dict[str, dict] = {}
    reviewed_this_day = []
    for row in rows:
        codes = parse_reason_codes(row.get("reason_codes"))
        posts = int(row.get("posts") or 0)
        bucket = bucket_for(row)
        if primary_day(row) == date:
            buckets[bucket]["docs"] += 1
            buckets[bucket]["posts"] += posts
            folder = str(row.get("folder_date") or "")
            slot = by_folder.setdefault(folder, _folder_slot())
            slot[bucket]["docs"] += 1
            slot[bucket]["posts"] += posts
            extra = [code for code in codes if code != "HELD_EXPIRED"]
            if bucket == "hold_expired":
                for code in extra:
                    buckets[bucket]["verdicts"][code] += 1
            elif bucket == "dropped":
                for code in extra:
                    buckets[bucket]["reasons"][code] += 1
        if bucket == "hold_expired" and reviewed_day(row) == date and primary_day(row) != date:
            reviewed_this_day.append({
                "folder_date": row.get("folder_date"),
                "file_name": row.get("file_name"),
                "work_key": row.get("work_key"),
                "reason_codes": codes,
                "held_at": row.get("held_at"),
                "expired_at": row.get("expired_at") or row.get("updated_at"),
            })
    for name in ("hold_expired", "dropped"):
        key = "verdicts" if name == "hold_expired" else "reasons"
        buckets[name][key] = dict(sorted(buckets[name][key].items(), key=lambda item: (-item[1], item[0])))
    for folder in by_folder:
        for name in BUCKETS:
            if name in ("hold_expired", "dropped"):
                by_folder[folder][name] = {
                    "docs": by_folder[folder][name]["docs"],
                    "posts": by_folder[folder][name]["posts"],
                }
    return {
        "date": date,
        "timezone": "America/Los_Angeles",
        "day_assignment": DAY_ASSIGNMENT_RULE,
        "buckets": buckets,
        "by_folder": dict(sorted(by_folder.items())),
        "hold_expired_reviewed_this_day": reviewed_this_day,
    }


def _count_line(name: str, bucket: dict) -> str:
    return f"- {bucket['label']}: {bucket['docs']} docs, {bucket['posts']} posts"


def _reason_lines(counts: dict) -> list[str]:
    lines = []
    for code, n in counts.items():
        lines.append(f"- {reason_code_label(code)} ({code}): {n}")
    return lines


def render_markdown(report: dict) -> str:
    buckets = report["buckets"]
    lines = [
        f"# Dropbox PDF cut — {report['date']} (America/Los_Angeles)",
        "",
        report["day_assignment"],
        "",
        "## Totals",
        "",
    ]
    for name in BUCKETS:
        lines.append(_count_line(name, buckets[name]))
    lines += ["", f"### {HOLD_EXPIRED_LABEL} — review verdicts", ""]
    lines += _reason_lines(buckets["hold_expired"].get("verdicts") or {}) or ["- none"]
    lines += ["", "### dropped — reasons", ""]
    lines += _reason_lines(buckets["dropped"].get("reasons") or {}) or ["- none"]
    lines += ["", "## By folder date", ""]
    if not report["by_folder"]:
        lines.append("- none")
    for folder, slot in report["by_folder"].items():
        parts = []
        for name in BUCKETS:
            docs = slot[name]["docs"]
            if not docs:
                continue
            posts = slot[name]["posts"]
            extra = f", {posts} posts" if posts else ""
            parts.append(f"{BUCKET_LABELS[name]} {docs}{extra}")
        lines.append(f"- {folder or '(blank)'}: " + "; ".join(parts))
    lines += ["", "## Hold-expired originally reviewed this day", ""]
    extra = report.get("hold_expired_reviewed_this_day") or []
    if not extra:
        lines.append("- none (held_at unknown or expiry day matches this report)")
    else:
        for row in extra:
            name = row.get("file_name") or row.get("work_key") or "(row)"
            lines.append(
                f"- {row.get('folder_date') or ''} {name}: "
                f"{', '.join(parse_reason_codes(row.get('reason_codes')))}"
            )
    lines.append("")
    return "\n".join(lines)


def _pt_bounds(date: str) -> tuple[str, str]:
    start = datetime.fromisoformat(date + "T00:00:00").replace(tzinfo=PT)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    )


def _cell(value):
    if isinstance(value, dict):
        return value.get("value")
    return value


def _hrana(url: str, token: str, sql: str, args: tuple) -> list[dict]:
    origin = url.replace("libsql://", "https://").replace("wss://", "https://")
    payload = json.dumps({
        "requests": [{
            "type": "execute",
            "stmt": {
                "sql": sql,
                "args": [{"type": "null"} if a is None else {"type": "text", "value": str(a)} for a in args],
            },
        }, {"type": "close"}],
    }).encode()
    req = urllib.request.Request(
        origin.rstrip("/") + "/v2/pipeline",
        data=payload,
        method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.load(resp)
    result = data["results"][0]
    if result.get("type") != "ok":
        raise RuntimeError(json.dumps(result)[:1200])
    cols = [c["name"] for c in result["response"]["result"]["cols"]]
    rows = []
    for raw in result["response"]["result"]["rows"]:
        rows.append({cols[i]: _cell(raw[i]) for i in range(len(cols))})
    return rows


def fetch_rows(date: str, url: str | None = None, token: str | None = None) -> list[dict]:
    url = url or os.environ.get("TURSO_DB_URL") or ""
    token = token or os.environ.get("TURSO_AUTH_TOKEN") or ""
    if not url or not token:
        raise SystemExit("TURSO_DB_URL and TURSO_AUTH_TOKEN are required for a live cut")
    start, end = _pt_bounds(date)
    try:
        return _hrana(url, token, CUT_SQL, (start, end, start, end, start, end))
    except RuntimeError as exc:
        if "no such column" not in str(exc).lower():
            raise
        return _hrana(url, token, CUT_SQL_LEGACY, (start, end))


def load_rows(path: str | Path) -> list[dict]:
    payload = json.loads(Path(path).read_text())
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"]
    if isinstance(payload, dict) and isinstance(payload.get("det"), list):
        return payload["det"]
    raise ValueError("JSON must be a row list or an object with rows/det")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="", help="PT calendar date YYYY-MM-DD (default yesterday PT)")
    parser.add_argument("--from-json", help="read rows from a JSON export instead of Turso")
    parser.add_argument("--json-out", help="write the report JSON")
    parser.add_argument("--md-out", help="write the report markdown")
    parser.add_argument("--counts-only", action="store_true",
                        help="omit document identifiers (for public CI artifacts)")
    args = parser.parse_args(argv)
    date = args.date.strip() or default_cut_date()
    rows = load_rows(args.from_json) if args.from_json else fetch_rows(date)
    report = build_report(rows, date)
    if args.counts_only:
        for row in report["hold_expired_reviewed_this_day"]:
            row.pop("file_name", None)
            row.pop("work_key", None)
    markdown = render_markdown(report)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n")
    if args.md_out:
        Path(args.md_out).write_text(markdown if markdown.endswith("\n") else markdown + "\n")
    summary = {name: {"docs": report["buckets"][name]["docs"], "posts": report["buckets"][name]["posts"]}
               for name in BUCKETS}
    print(json.dumps({"date": date, "buckets": summary, "det_n": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
