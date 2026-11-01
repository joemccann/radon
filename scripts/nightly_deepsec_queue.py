#!/usr/bin/env python3
"""Re-verify DeepSec operator-only findings before they reach PR Next.

The loop used to copy every carried operator-only item into Next. This
helper is the deterministic half: it applies the committed closed ledger,
merges agent verdicts (open / closed / unverifiable), keeps closed IDs in
``last-audited.json`` as a durable record, and emits Next text that omits
them. The operator-written accepted-risk ledger parks an item as
``accepted`` until its expiry; an expired acceptance returns the item to
Next to be re-decided. Next is capped at three lines plus a counts-only
overflow line. Stdlib only. No issue comments, no host mutation, no secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

STATES = ("open", "closed", "unverifiable")
FINDING_ID_RE = re.compile(r"^DS-\d{4}-\d{2}-\d{2}-\d{2}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPO = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO / "docs" / "security-deepsec-closed.json"
DEFAULT_ACCEPTED = REPO / "docs" / "security-accepted-risks.json"
MAX_ACCEPT_DAYS = 90
NEXT_CAP = 3
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unreadable JSON: {path}") from exc


def load_ledger(path: Path | None = None) -> dict[str, dict[str, Any]]:
    raw = _as_dict(load_json(path or DEFAULT_LEDGER))
    out: dict[str, dict[str, Any]] = {}
    for row in raw.get("closed") or []:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or "")
        if not FINDING_ID_RE.fullmatch(item_id):
            raise ValueError(f"ledger id is not a sanitized DS- id: {item_id!r}")
        out[item_id] = dict(row)
    return out


def parse_date(value: Any) -> date:
    text = str(value or "")
    if not ISO_DATE_RE.fullmatch(text):
        raise ValueError(f"not an ISO date (YYYY-MM-DD): {text!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"not an ISO date (YYYY-MM-DD): {text!r}") from exc


def load_accepted(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Validate the accepted-risk ledger. Any malformed row fails closed."""
    raw = load_json(path or DEFAULT_ACCEPTED)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("accepted-risk ledger must be a version 1 object")
    rows = raw.get("accepted")
    if not isinstance(rows, list):
        raise ValueError("accepted-risk ledger 'accepted' must be a list")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"accepted-risk row is not an object: {row!r}")
        item_id = str(row.get("id") or "")
        if not FINDING_ID_RE.fullmatch(item_id):
            raise ValueError(f"accepted-risk id is not a sanitized DS- id: {item_id!r}")
        if item_id in out:
            raise ValueError(f"accepted-risk id listed twice: {item_id}")
        for key in ("reason", "accepted_by"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"accepted-risk {item_id} needs a non-empty {key}")
        accepted_at = parse_date(row.get("accepted_at"))
        expires_at = parse_date(row.get("expires_at"))
        if expires_at <= accepted_at:
            raise ValueError(f"accepted-risk {item_id} expires_at must be after accepted_at")
        if (expires_at - accepted_at).days > MAX_ACCEPT_DAYS:
            raise ValueError(
                f"accepted-risk {item_id} expires_at is more than {MAX_ACCEPT_DAYS} days out"
            )
        out[item_id] = dict(row)
    return out


def _is_operator_only(item: dict[str, Any]) -> bool:
    if item.get("operator_only") is True:
        return True
    disposition = str(item.get("disposition") or item.get("kind") or item.get("type") or "")
    return disposition == "operator-only"


def _coerce_item(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str) and FINDING_ID_RE.fullmatch(raw.strip()):
        return {"id": raw.strip(), "disposition": "operator-only"}
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id") or "").strip()
    if not FINDING_ID_RE.fullmatch(item_id):
        return None
    item = dict(raw)
    item["id"] = item_id
    return item


def load_last_audited(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"open_queue": [], "closed_queue": []}
    raw = _as_dict(load_json(path))
    open_queue = raw.get("open_queue")
    if open_queue is None:
        open_queue = raw.get("queue")
    if open_queue is None:
        open_queue = raw.get("findings")
    closed_queue = raw.get("closed_queue") or []
    return {
        **raw,
        "open_queue": list(open_queue or []),
        "closed_queue": list(closed_queue or []),
    }


def load_verdicts(raw: Any) -> dict[str, dict[str, Any]]:
    rows = raw
    if isinstance(raw, dict):
        rows = raw.get("verdicts") or raw.get("items") or []
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or "").strip()
        if not FINDING_ID_RE.fullmatch(item_id):
            raise ValueError(f"verdict id is not a sanitized DS- id: {item_id!r}")
        state = str(row.get("state") or "").strip()
        if state not in STATES:
            raise ValueError(f"verdict {item_id} state must be one of {STATES}")
        out[item_id] = dict(row)
        out[item_id]["id"] = item_id
        out[item_id]["state"] = state
    return out


def _evidence_text(row: dict[str, Any]) -> str:
    if row.get("evidence"):
        return str(row["evidence"])
    parts = []
    if row.get("pr"):
        parts.append(f"PR #{row['pr']}")
    if row.get("commit"):
        parts.append(str(row["commit"]))
    path = row.get("path") or row.get("file")
    lines = row.get("lines")
    if path and lines:
        parts.append(f"{path} L{lines}")
    elif path:
        parts.append(str(path))
    return ", ".join(parts)


def _closed_record(item_id: str, source: dict[str, Any]) -> dict[str, Any]:
    record = {
        "id": item_id,
        "disposition": "operator-only",
        "state": "closed",
        "closed_at": str(source.get("closed_at") or ""),
        "evidence": _evidence_text(source),
    }
    for key in ("pr", "commit", "path", "lines"):
        if source.get(key) not in (None, ""):
            record[key] = source[key]
    if source.get("action"):
        record["action"] = source["action"]
    return record


def classify_item(
    item: dict[str, Any],
    *,
    ledger: dict[str, dict[str, Any]],
    prior_closed: dict[str, dict[str, Any]],
    verdict: dict[str, Any] | None,
) -> dict[str, Any]:
    item_id = item["id"]
    prior = ledger.get(item_id) or prior_closed.get(item_id)
    action = str((verdict or {}).get("action") or item.get("action") or item.get("summary") or "")
    if prior:
        new_evidence = str((verdict or {}).get("new_evidence") or "").strip()
        if verdict and verdict.get("state") == "open" and new_evidence:
            return {
                "id": item_id,
                "disposition": "operator-only",
                "state": "open",
                "reopened": True,
                "evidence": new_evidence,
                "action": action,
            }
        record = _closed_record(item_id, {**prior, **item})
        if action:
            record["action"] = action
        return record
    if verdict:
        state = verdict["state"]
        classified = {
            "id": item_id,
            "disposition": "operator-only",
            "state": state,
            "action": action,
        }
        if state == "closed":
            classified.update(_closed_record(item_id, {**item, **verdict}))
        elif state == "open":
            classified["evidence"] = str(verdict.get("evidence") or verdict.get("new_evidence") or "")
            if not classified["evidence"]:
                raise ValueError(f"{item_id} is open but cites no evidence")
        else:
            classified["missing"] = str(verdict.get("missing") or "re-verify evidence was not supplied")
        return classified
    return {
        "id": item_id,
        "disposition": "operator-only",
        "state": "unverifiable",
        "action": action,
        "missing": "no re-verify verdict against origin/main",
    }


def apply_acceptance(
    item: dict[str, Any], accepted: dict[str, dict[str, Any]], today: date
) -> dict[str, Any]:
    """Park a non-closed item as accepted, or mark a lapsed acceptance."""
    row = accepted.get(item["id"])
    if not row or item.get("state") == "closed":
        return item
    expires_at = str(row["expires_at"])
    if parse_date(expires_at) < today:
        return {**item, "acceptance_expired": expires_at}
    return {
        **item,
        "state": "accepted",
        "prior_state": item.get("state"),
        "expires_at": expires_at,
        "accepted_at": str(row["accepted_at"]),
        "accepted_by": str(row["accepted_by"]),
        "reason": str(row["reason"]),
    }


def _next_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    if item.get("acceptance_expired"):
        group = 0
    elif item.get("state") == "open":
        group = 1
    else:
        group = 2
    severity = SEVERITY_RANK.get(str(item.get("severity") or "").upper(), len(SEVERITY_RANK))
    return (group, severity, item["id"])


def next_text(items: list[dict[str, Any]]) -> str:
    pending = sorted(
        (item for item in items if item.get("state") in ("open", "unverifiable")),
        key=_next_sort_key,
    )
    lines: list[str] = []
    for item in pending[:NEXT_CAP]:
        item_id = item["id"]
        action = str(item.get("action") or "").strip() or "operator action still required"
        suffix = ""
        if item.get("acceptance_expired"):
            suffix = f" (acceptance expired {item['acceptance_expired']}: re-decide)"
        if item.get("state") == "unverifiable":
            missing = str(item.get("missing") or "needed evidence is missing")
            lines.append(f"- **{item_id}** (unverifiable): {action} Missing: {missing}.{suffix}")
        else:
            lines.append(f"- **{item_id}**: {action}{suffix}")
    overflow = len(pending) - NEXT_CAP
    if overflow > 0:
        lines.append(f"- {overflow} more operator-only item(s) in the private report.")
    return "\n".join(lines)


def write_private(path: Path, payload: dict[str, Any]) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def reverify(
    *,
    last_audited_path: Path,
    verdicts: Any = None,
    ledger_path: Path | None = None,
    accepted_ledger_path: Path | None = None,
    today: date | None = None,
    write: bool = False,
) -> dict[str, Any]:
    state = load_last_audited(last_audited_path)
    ledger = load_ledger(ledger_path)
    accepted = load_accepted(accepted_ledger_path)
    today = today or datetime.now(timezone.utc).date()
    verdict_map = load_verdicts(verdicts)
    prior_closed: dict[str, dict[str, Any]] = {}
    for raw in state.get("closed_queue") or []:
        item = _coerce_item(raw)
        if item:
            prior_closed[item["id"]] = item

    classified: list[dict[str, Any]] = []
    seen: set[str] = set()
    passthrough: list[Any] = []
    for raw in state.get("open_queue") or []:
        item = _coerce_item(raw)
        if item is None or not _is_operator_only(item):
            passthrough.append(raw)
            continue
        seen.add(item["id"])
        classified.append(
            classify_item(
                item,
                ledger=ledger,
                prior_closed=prior_closed,
                verdict=verdict_map.get(item["id"]),
            )
        )
    for item_id, verdict in verdict_map.items():
        if item_id in seen:
            continue
        classified.append(
            classify_item(
                {"id": item_id, "disposition": "operator-only", "action": verdict.get("action")},
                ledger=ledger,
                prior_closed=prior_closed,
                verdict=verdict,
            )
        )
        seen.add(item_id)
    for item_id, row in ledger.items():
        if item_id in seen or item_id in prior_closed:
            continue
        classified.append(_closed_record(item_id, row))
        seen.add(item_id)
    classified = [apply_acceptance(item, accepted, today) for item in classified]

    closed_ids = {item["id"] for item in classified if item["state"] == "closed"}
    closed_tonight = [
        item for item in classified if item["state"] == "closed" and item["id"] not in prior_closed
    ]
    open_queue = list(passthrough)
    open_queue.extend(item for item in classified if item["state"] != "closed")
    closed_queue = [item for item in (state.get("closed_queue") or []) if _coerce_item(item)]
    closed_by_id = {item["id"]: item for item in closed_queue if item.get("id")}
    for item in classified:
        if item["state"] == "closed":
            closed_by_id[item["id"]] = item
        elif item.get("reopened"):
            closed_by_id.pop(item["id"], None)
    updated = {
        **{k: v for k, v in state.items() if k not in {"open_queue", "closed_queue", "queue", "findings"}},
        "open_queue": open_queue,
        "closed_queue": list(closed_by_id.values()),
    }
    if write:
        write_private(last_audited_path, updated)
    return {
        "items": classified,
        "next": next_text(classified),
        "closed_tonight": closed_tonight,
        "closed_ids": sorted(closed_ids),
        "accepted": [
            {
                "id": item["id"],
                "expires_at": item["expires_at"],
                "accepted_by": item["accepted_by"],
                "reason": item["reason"],
            }
            for item in classified
            if item["state"] == "accepted"
        ],
        "expired_acceptances": [
            {"id": item["id"], "expires_at": item["acceptance_expired"]}
            for item in classified
            if item.get("acceptance_expired")
        ],
        "state": updated,
    }


def _load_verdicts_arg(path: str | None) -> Any:
    if not path:
        return []
    return load_json(Path(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("command", choices=("reverify",))
    parser.add_argument("--last-audited", required=True)
    parser.add_argument("--verdicts", default="")
    parser.add_argument("--ledger", default="")
    parser.add_argument("--accepted-ledger", default="")
    parser.add_argument("--today", default="", help="YYYY-MM-DD; defaults to the UTC date")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = reverify(
        last_audited_path=Path(args.last_audited),
        verdicts=_load_verdicts_arg(args.verdicts or None),
        ledger_path=Path(args.ledger) if args.ledger else None,
        accepted_ledger_path=Path(args.accepted_ledger) if args.accepted_ledger else None,
        today=parse_date(args.today) if args.today else None,
        write=args.write,
    )
    public = {
        key: result[key]
        for key in (
            "items",
            "next",
            "closed_tonight",
            "closed_ids",
            "accepted",
            "expired_acceptances",
        )
    }
    if args.json:
        json.dump(public, sys.stdout)
        sys.stdout.write("\n")
    else:
        if result["next"]:
            sys.stdout.write(result["next"] + "\n")
        else:
            sys.stdout.write("Fixed with green deployment\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc
