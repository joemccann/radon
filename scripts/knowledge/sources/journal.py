"""Journal connector: one doc per Turso `journal` row (fill/trade rendering)."""
from __future__ import annotations

import json
from typing import Iterator

from ..schema import KnowledgeDoc

SOURCE = "journal"
SCOPE = "trading"

TRADE_LOG_KEY_PREFIX = "trade_log:"

_BATCH_ROWS = 200
_ROWS_SQL = (
    "SELECT trade_id, payload, filled_at, written_at FROM journal "
    "WHERE trade_id > ? ORDER BY trade_id LIMIT ?"
)


def fetch(db) -> Iterator[KnowledgeDoc]:
    yield from _journal_rows(db)


def prunable_doc_keys(doc_keys: Iterator[str] | list[str]) -> list[str]:
    """Vanished-key authority: Turso-backed journal rows only. trade_log:* docs
    were ingested from the retired trade log and are the only remaining copy of
    those theses in Turso, so nothing here may prune them."""
    return [key for key in doc_keys if not key.startswith(TRADE_LOG_KEY_PREFIX)]


def _journal_rows(db) -> Iterator[KnowledgeDoc]:
    cursor = ""
    while True:
        rows = db.execute(_ROWS_SQL, (cursor, _BATCH_ROWS)).fetchall()
        if not rows:
            return
        for trade_id, payload_json, filled_at, written_at in rows:
            payload = _parse_payload(payload_json)
            if payload is None:
                continue
            yield KnowledgeDoc(
                source=SOURCE,
                scope=SCOPE,
                doc_key=trade_id,
                content=_render_trade(payload),
                title=_title(payload),
                metadata=_metadata(payload),
                created_at=written_at,
                last_activity_at=written_at or _date_to_iso(filled_at),
            )
        cursor = rows[-1][0]


def _parse_payload(payload_json: str) -> dict | None:
    try:
        payload = json.loads(payload_json)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _render_trade(payload: dict) -> str:
    if payload.get("decision") == "IB_AUTO_IMPORT":
        return _render_fill(payload)
    return _render_rationale(payload)


def _render_fill(payload: dict) -> str:
    parts = [_header(payload)]
    detail = []
    if payload.get("contracts") is not None:
        detail.append(f"{payload['contracts']} contracts")
    if payload.get("fill_price") is not None:
        detail.append(f"@ {payload['fill_price']}")
    if payload.get("total_cost") is not None:
        detail.append(f"total ${payload['total_cost']:,.2f}")
    if payload.get("commission") is not None:
        detail.append(f"commission ${payload['commission']:,.2f}")
    if detail:
        parts.append(", ".join(detail) + ".")
    if payload.get("notes"):
        parts.append(str(payload["notes"]))
    return " ".join(parts)


def _render_rationale(payload: dict) -> str:
    lines = [_header(payload)]
    contract_line = _contract_line(payload)
    if contract_line:
        lines.append(contract_line)
    if payload.get("gates_passed"):
        lines.append("Gates: " + ", ".join(str(g) for g in payload["gates_passed"]) + ".")
    if payload.get("thesis"):
        lines.append(f"Thesis: {payload['thesis']}")
    if payload.get("risk_factors"):
        lines.append("Risk factors: " + "; ".join(str(r) for r in payload["risk_factors"]) + ".")
    if payload.get("target_exit"):
        lines.append(f"Exit: {payload['target_exit']}")
    if payload.get("stop_loss"):
        lines.append(f"Stop: {payload['stop_loss']}")
    if payload.get("notes"):
        lines.append(f"Notes: {payload['notes']}")
    return "\n".join(lines)


def _contract_line(payload: dict) -> str | None:
    contract = payload.get("contract")
    if not contract:
        return None
    pieces = []
    if payload.get("contracts") is not None:
        pieces.append(f"{payload['contracts']}x {contract}")
    else:
        pieces.append(str(contract))
    if payload.get("fill_price") is not None:
        pieces.append(f"@ {payload['fill_price']}")
    if payload.get("total_cost") is not None:
        pieces.append(f"${payload['total_cost']:,.2f}")
    if payload.get("pct_of_bankroll") is not None:
        pieces.append(f"({payload['pct_of_bankroll']}% bankroll)")
    return "Contract: " + " ".join(pieces) + "."


def _header(payload: dict) -> str:
    label = " ".join(str(x) for x in (payload.get("date"), payload.get("ticker")) if x)
    origin = ", ".join(
        str(x) for x in (payload.get("company_name"), payload.get("sector")) if x
    )
    if origin:
        label = f"{label} ({origin})" if label else origin
    tail = " ".join(str(x) for x in (payload.get("action"), payload.get("structure")) if x)
    if label and tail:
        return f"{label} — {tail}."
    return f"{label or tail}."


def _title(payload: dict) -> str | None:
    parts = [str(x) for x in (payload.get("ticker"), payload.get("structure")) if x]
    if not parts:
        return None
    title = " ".join(parts)
    if payload.get("date"):
        title += f" ({payload['date']})"
    return title


def _metadata(payload: dict) -> dict:
    fields = {
        "ticker": payload.get("ticker"),
        "action": payload.get("action"),
        "structure": payload.get("structure"),
        "date": payload.get("date"),
        "decision": payload.get("decision"),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _date_to_iso(date_str: str | None) -> str | None:
    if not date_str:
        return None
    if "T" in date_str:
        return date_str
    return f"{date_str}T00:00:00Z"
