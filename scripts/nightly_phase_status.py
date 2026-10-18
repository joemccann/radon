#!/usr/bin/env python3
"""Machine-readable status for a nightly runner phase.

The harness (``scripts/runner/phase_status.sh``, called from ``run_loop.sh``
and the security post-run hook) emits this from the process exit. Agents do
not. A report parses the line and, for older comments, falls back to the
banner and phase-stamp wording.

::

    <!-- radon-phase loop=testing phase=audit exit=0 audited-through=<sha> ts=<utc> -->
    radon-phase loop=testing phase=audit exit=0 audited-through=<sha> ts=<utc>
    audited-through: <sha>

``audited-through`` is present only when the audit phase exited 0. The colon
form is the cursor ``nightly_audit_context`` and ``nightly_issue_prune``
already match. A non-zero exit never advances that cursor.

Exit codes:

====  ================================================================
0     OK
1     FAILED (agent or post-run hook failed, or the hook gave no status)
2     REFUSED (a guard stopped the phase before the agent ran)
70    GROUND TRUTH FAILED
75    INCOMPLETE (resumable)
124   TIMEOUT
====  ================================================================

A signalled phase (runner exit 143) posts 1. The deliver hook may classify a
zero process exit as 75 when the phase did not finish.

Stdlib only. No network.
"""
from __future__ import annotations

import re

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_GROUND_TRUTH = 70
EXIT_INCOMPLETE = 75
EXIT_TIMEOUT = 124

EXIT_NAMES = {
    EXIT_OK: "OK",
    EXIT_FAILED: "FAILED",
    EXIT_REFUSED: "REFUSED",
    EXIT_GROUND_TRUTH: "GROUND TRUTH FAILED",
    EXIT_INCOMPLETE: "INCOMPLETE",
    EXIT_TIMEOUT: "TIMEOUT",
}

_TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HTML = re.compile(r"<!--\s*radon-phase\s+(?P<fields>[^>]*?)\s*-->")
_VISIBLE = re.compile(r"(?m)^radon-phase\s+(?P<fields>\S.*?)\s*$")
_FIELD = re.compile(r"(?:^|\s)(loop|phase|exit|audited-through|ts)=(\S+)")
_MARKER = re.compile(r"audited-through:?\**\s*`?([0-9a-f]{7,40})\b", re.IGNORECASE)

# Older dead-man banners (docs/dead-man-comment-format.md).
_BANNER = re.compile(
    r"\*\*(Healthy|Fixed|Needs you|Incomplete)\*\*"
    r"\s*[·•]\s*([a-z0-9-]+)\s*[·•]\s*\d{4}-\d{2}-\d{2}\s*[·•]\s*([^\n]+)",
    re.IGNORECASE,
)
# Older security phase stamp: **audit** STAMP **OK**
_STAMP = re.compile(
    r"\*\*([a-z0-9-]+)\*\*\s+\S+\s+\*\*([^*]+?)\*\*",
    re.IGNORECASE,
)
_LEAD_EXIT = {
    "healthy": EXIT_OK,
    "fixed": EXIT_OK,
    "needs you": EXIT_OK,
    "incomplete": EXIT_INCOMPLETE,
}


def phase_exit(
    rc,
    *,
    refused: bool = False,
    killed: bool = False,
    hook_status: str = "",
    hook_required: bool = False,
) -> int:
    """Map a phase's process exit and harness status to a public exit code.

    ``hook_status`` is the post-run hook's ``status=`` line, never agent text.
    A named process exit (2, 70, 124) wins over that line. A zero exit is 75
    when the hook says the phase did not finish, and 1 when a required hook
    printed no status.
    """
    if killed:
        return EXIT_FAILED
    try:
        code = int(rc)
    except (TypeError, ValueError):
        code = EXIT_FAILED
    if code == 143:
        return EXIT_FAILED
    if refused or code == EXIT_REFUSED:
        return EXIT_REFUSED
    if code == EXIT_TIMEOUT:
        return EXIT_TIMEOUT
    if code == EXIT_GROUND_TRUTH:
        return EXIT_GROUND_TRUTH
    status = (hook_status or "").strip()
    if hook_required and not status and not refused:
        return EXIT_FAILED
    if status.startswith("INCOMPLETE") or status.startswith("TRUNCATED"):
        return EXIT_INCOMPLETE
    if status.startswith("TIMEOUT"):
        return EXIT_TIMEOUT
    if status.startswith("REFUSED"):
        return EXIT_REFUSED
    if "GROUND TRUTH" in status:
        return EXIT_GROUND_TRUTH
    if status.startswith(("FAILED", "KILLED", "CRASHED")):
        return EXIT_FAILED
    if status.startswith("OK"):
        return EXIT_OK
    if code == EXIT_OK:
        return EXIT_OK
    if code == EXIT_INCOMPLETE:
        return EXIT_INCOMPLETE
    return EXIT_FAILED


def format_phase_comment(
    *,
    loop: str,
    phase: str,
    exit_code: int,
    audited_through: str = "",
    timestamp: str,
) -> str:
    """HTML comment, the same fields on a visible line, and the cursor.

    The sha is included only for ``phase=audit`` with exit 0. Anything else
    is dropped so a failed or non-audit phase cannot move the checkpoint.
    """
    loop_token = (loop or "").strip()
    phase_token = (phase or "").strip()
    if not _TOKEN.match(loop_token) or not _TOKEN.match(phase_token):
        raise ValueError("loop and phase must be lowercase tokens")
    try:
        code = int(exit_code)
    except (TypeError, ValueError) as exc:
        raise ValueError("exit must be an integer") from exc
    if code < 0:
        raise ValueError("exit must be an integer")
    if not _TS.match(timestamp or ""):
        raise ValueError("timestamp must be UTC YYYY-MM-DDTHH:MM:SSZ")
    sha = (audited_through or "").strip().lower()
    if code != EXIT_OK or phase_token != "audit" or not _SHA.match(sha):
        sha = ""
    fields = f"loop={loop_token} phase={phase_token} exit={code}"
    if sha:
        fields += f" audited-through={sha}"
    fields += f" ts={timestamp}"
    lines = [f"<!-- radon-phase {fields} -->", f"radon-phase {fields}"]
    if sha:
        lines.append(f"audited-through: {sha}")
    return "\n".join(lines) + "\n"


def _fields(raw: str) -> dict[str, str] | None:
    found = dict(_FIELD.findall(raw or ""))
    loop = found.get("loop", "")
    phase = found.get("phase", "")
    exit_raw = found.get("exit", "")
    if not _TOKEN.match(loop) or not _TOKEN.match(phase) or not exit_raw.isdigit():
        return None
    sha = found.get("audited-through", "").lower()
    if sha and not _SHA.match(sha):
        sha = ""
    return {
        "loop": loop,
        "phase": phase,
        "exit": int(exit_raw),
        "audited_through": sha,
        "timestamp": found.get("ts", ""),
        "source": "marker",
    }


def parse_phase_statuses(body: str) -> list[dict]:
    """Every machine-readable phase line in ``body``, HTML comment first."""
    text = body or ""
    rows = []
    seen = set()
    for match in list(_HTML.finditer(text)) + list(_VISIBLE.finditer(text)):
        row = _fields(match.group("fields"))
        if row is None:
            continue
        key = (row["loop"], row["phase"], row["exit"], row["audited_through"], row["timestamp"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def parse_phase_status(body: str) -> dict | None:
    rows = parse_phase_statuses(body)
    return rows[0] if rows else None


def _legacy_exit(status: str) -> int:
    text = (status or "").strip()
    upper = text.upper()
    if upper.startswith("INCOMPLETE") or upper.startswith("TRUNCATED"):
        return EXIT_INCOMPLETE
    if upper.startswith("TIMEOUT"):
        return EXIT_TIMEOUT
    if upper.startswith("REFUSED"):
        return EXIT_REFUSED
    if "GROUND TRUTH" in upper:
        return EXIT_GROUND_TRUTH
    if upper.startswith(("FAILED", "KILLED", "CRASHED")):
        return EXIT_FAILED
    if upper.startswith("OK"):
        return EXIT_OK
    lead = _LEAD_EXIT.get(text.lower())
    if lead is not None:
        return lead
    return EXIT_FAILED


def infer_legacy_status(body: str) -> dict | None:
    """Older comments: banner lead word, else the phase-stamp status word."""
    text = body or ""
    banner = _BANNER.search(text)
    if banner:
        lead, loop, phase = banner.group(1), banner.group(2).lower(), banner.group(3).strip()
        sha_match = _MARKER.search(text)
        return {
            "loop": loop,
            "phase": phase,
            "exit": _legacy_exit(lead),
            "audited_through": sha_match.group(1).lower() if sha_match else "",
            "timestamp": "",
            "source": "legacy",
        }
    stamp = _STAMP.search(text)
    if stamp:
        sha_match = _MARKER.search(text)
        return {
            "loop": "",
            "phase": stamp.group(1).lower(),
            "exit": _legacy_exit(stamp.group(2)),
            "audited_through": sha_match.group(1).lower() if sha_match else "",
            "timestamp": "",
            "source": "legacy",
        }
    return None


def read_phase_status(body: str) -> dict | None:
    """Machine line when present, otherwise the legacy banner or stamp."""
    parsed = parse_phase_status(body)
    if parsed is not None:
        return parsed
    return infer_legacy_status(body)


def cursor_allowed(body: str) -> bool:
    """False when a machine line says this comment must not advance the cursor.

    Comments with no machine line stay eligible, so older checkpoints still count.
    """
    parsed = parse_phase_status(body)
    if parsed is None:
        return True
    return parsed["exit"] == EXIT_OK and parsed["phase"] == "audit"
