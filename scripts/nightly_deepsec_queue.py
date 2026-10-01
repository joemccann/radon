#!/usr/bin/env python3
"""Re-verify DeepSec operator-only findings before they reach PR Next.

The loop used to copy every carried operator-only item into Next. This
helper is the deterministic half: it applies the committed closed ledger,
merges agent verdicts (open / closed / unverifiable), keeps closed IDs in
``last-audited.json`` as a durable record, and emits Next text that omits
them. Stdlib only. No issue comments, no host mutation, no secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

STATES = ("open", "closed", "unverifiable")
FINDING_ID_RE = re.compile(r"^DS-\d{4}-\d{2}-\d{2}-\d{2}$")
REPO = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO / "docs" / "security-deepsec-closed.json"


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


def next_text(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        if item.get("state") == "closed":
            continue
        item_id = item["id"]
        action = str(item.get("action") or "").strip() or "operator action still required"
        if item.get("state") == "unverifiable":
            missing = str(item.get("missing") or "needed evidence is missing")
            lines.append(f"- **{item_id}** (unverifiable): {action} Missing: {missing}.")
        else:
            lines.append(f"- **{item_id}**: {action}")
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
    write: bool = False,
) -> dict[str, Any]:
    state = load_last_audited(last_audited_path)
    ledger = load_ledger(ledger_path)
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
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = reverify(
        last_audited_path=Path(args.last_audited),
        verdicts=_load_verdicts_arg(args.verdicts or None),
        ledger_path=Path(args.ledger) if args.ledger else None,
        write=args.write,
    )
    public = {
        "items": result["items"],
        "next": result["next"],
        "closed_tonight": result["closed_tonight"],
        "closed_ids": result["closed_ids"],
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
