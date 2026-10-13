"""Parse and redact ``journalctl -o json`` output for the /admin log disclosure.

Shared by the host control daemon (``scripts/control_service/serve.py``) and
the API's direct-systemd path (``scripts/api/service_logs.py``). Stdlib only,
so the daemon can import it without the API stack.

The journalctl argv is fixed here and in ``cloud/scripts/operator-radon.sh``
(``radon logs``); no caller supplies an option.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

LOG_LINES = 300
MAX_LOG_MESSAGE_CHARS = 2000
# Redaction runs on at most this much of one message; the cap trims the rest.
_REDACT_WINDOW_CHARS = 8000
OMITTED_MESSAGE = "(message too large: journalctl JSON omits fields over 4096 bytes)"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def journal_argv(unit: str) -> list[str]:
    """The exact argv ``radon logs <unit>`` runs. Callers validate ``unit``."""
    return [
        "journalctl", "-u", unit, "-n", str(LOG_LINES), "--no-pager", "-q",
        "-o", "json", "--output-fields=MESSAGE,PRIORITY",
    ]


_SECRET_NAME = r"(?:token|secret|passw(?:or)?d|api[_-]?key|access[_-]?key|private[_-]?key|\bkey)"
_REDACTIONS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-[REDACTED]"),
    # JWTs of any length (a short one slips under the long-token rules).
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}(?:\.[A-Za-z0-9_-]*)?"), "[REDACTED]"),
    # scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^/\s@]+@"), r"\1[REDACTED]@"),
    (
        re.compile(r'(?i)("[A-Za-z0-9_.-]*' + _SECRET_NAME + r'[A-Za-z0-9_]*"\s*:\s*")([^"]*)(")'),
        r"\1[REDACTED]\3",
    ),
    # `password: x`, `x-api-key: x`, `'api_key': 'x'` (headers, dict reprs).
    # The name must END in the secret word, so `KeyError: '...'` survives.
    (
        re.compile(r"""(?i)((?<![A-Za-z0-9])['"]?[A-Za-z0-9_.-]*""" + _SECRET_NAME
                   + r"""['"]?\s*:\s*['"]?)([^\s'",;&}]+)"""),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)([A-Za-z0-9_.-]*" + _SECRET_NAME + r"[A-Za-z0-9_]*=)([^\s&;,\"']+)"),
        r"\1[REDACTED]",
    ),
    # 41+ hex chars: keys and digests. A 40-char git SHA and a 32-char page id
    # stay readable because they are what an operator greps for.
    (re.compile(r"\b[0-9a-fA-F]{41,}\b"), "[REDACTED]"),
)
_BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+_-])[A-Za-z0-9+_-]{40,}={0,2}")


def _redact_base64(match: re.Match) -> str:
    text = match.group(0)
    if (
        any(c.isupper() for c in text)
        and any(c.islower() for c in text)
        and any(c.isdigit() for c in text)
    ):
        return "[REDACTED]"
    return text


def redact_secrets(text: str) -> str:
    """Mask bearer tokens, key=/token=/secret=/password= values and long tokens."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return _BASE64_RUN.sub(_redact_base64, text)


def _iso_from_realtime(raw: object) -> str | None:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    try:
        micros = int(raw)
        stamp = _EPOCH + timedelta(microseconds=micros)
    except (TypeError, ValueError, OverflowError):
        return None
    if micros < 0:
        return None
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _priority(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 0 <= value <= 7 else None


def _message(raw: object) -> str | None:
    if raw is None:
        return OMITTED_MESSAGE
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        # journald emits non-UTF-8 or binary field data as a byte array.
        try:
            return bytes(raw).decode("utf-8", errors="replace")
        except (TypeError, ValueError):
            return None
    return str(raw)


def fit_reply_budget(entries: list[dict], max_bytes: int) -> tuple[list[dict], bool]:
    """Drop the oldest entries until their JSON encoding fits ``max_bytes``.

    ``json.dumps`` escapes non-ASCII, so 300 capped messages can encode to
    several MB and overflow the control socket's reply cap. Returns the kept
    entries (still oldest first) and whether any were dropped.
    """
    kept: list[dict] = []
    total = 2  # the enclosing []
    for entry in reversed(entries):
        size = len(json.dumps(entry, separators=(",", ":"))) + 1
        if total + size > max_bytes:
            break
        kept.append(entry)
        total += size
    kept.reverse()
    return kept, len(kept) < len(entries)


def parse_journal_json(raw: str, limit: int = LOG_LINES) -> tuple[list[dict], bool]:
    """``journalctl -o json`` lines -> ([{ts, priority, message}], truncated).

    Entries keep journalctl's order (oldest first). A line that is not a JSON
    object, or carries no usable ``__REALTIME_TIMESTAMP``, is skipped.
    ``truncated`` is True when the tail limit was reached, so older history
    exists that this reply does not carry.
    """
    lines = [line for line in (raw or "").splitlines() if line.strip()]
    entries: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ts = _iso_from_realtime(row.get("__REALTIME_TIMESTAMP"))
        if ts is None:
            continue
        message = _message(row.get("MESSAGE"))
        if message is None:
            continue
        message = redact_secrets(message[:_REDACT_WINDOW_CHARS])[:MAX_LOG_MESSAGE_CHARS]
        entries.append({"ts": ts, "priority": _priority(row.get("PRIORITY")), "message": message})
    return entries, len(lines) >= limit
