#!/usr/bin/env python3
"""Format GitHub ISSUE write-ups for the non-security nightly agents.

Operators read the rolling issues (security, testing, reliability, CI
performance). Status dumps and pointers to a log file on a
machine are not a write-up.

This module is the agent three-section spec and CLI. The harness posts a
separate machine-readable phase line (scripts/runner/phase_status.sh) from
the process exit and never execs this module to do it. Wrappers create the
issue once with a timeless rolling-dead-man description.

Agent write-up shape:

    **Issue discovered**
    ...

    **What was done to fix it**
    ...

    **Next**
    ...

Next is only for work that still must happen OUTSIDE of CI pushing a new
deployment. If nothing remains, Next is "Fixed with green deployment".

Security agents do not write GitHub issue comments. The harness posts the
only public security comment: loop, phase, exit code, sha and timestamp.

Stdlib only, plus the canonical scrubber (scripts/credential_redaction.py).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys

from credential_redaction import EXECUTION_ID_PATTERNS, build_block_scalar_re

HEADING_DISCOVERED = "Issue discovered"
HEADING_DONE = "What was done to fix it"
HEADING_NEXT = "Next"
FIXED_NEXT = "Fixed with green deployment"
NO_RUN_DISCOVERED = "No run yet."
NO_RUN_DONE = "Nothing this run."
NO_RUN_NEXT = "Waiting for the first nightly cycle."
NOTHING_DONE = "Nothing this run."
RESUME_NEXT = (
    "The next fire resumes this phase. Do not read this as a finished run."
)
QUOTA_NEXT = (
    "Top up at claude.ai/settings/usage, then let the next fire resume."
)
TIMEOUT_NEXT = (
    "The next fire resumes. Partial work may exist on the nightly branch."
)
RETRY_NEXT = "The next fire retries this phase."
REDACTED = "[REDACTED]"

_FENCE_RE = re.compile(r"```.*?```", re.S)
# A cookie header can contain multiple opaque values separated by spaces.
# Redact its entire value while preserving diagnostics on following lines.
_COOKIE_HEADER_RE = re.compile(r"(\b(?:set-)?cookie[ \t]*:[ \t]*)[^\r\n]*", re.I)
# The key may be quoted (JSON) or a hyphenated header name, the value quoted
# (spaces inside) or led by an auth scheme word ("Authorization: Basic <value>").
# Unbounded identifier runs on BOTH sides of the alternation share its
# alphabet, so the engine redistributes prefix and suffix at every offset and
# the scan is quadratic in the length of an identifier run: measured 0.0084s,
# 0.0336s, 0.1371s and 0.5519s for 250, 500, 1000 and 2000 repetitions of a
# credential word. This sanitizer runs over contributor-shaped source excerpts
# in PR bodies, so that is a delivery stall.
#
# The fix is NOT a bound on the leading run: a bound is an evasion, because a
# key whose prefix is longer than it stops matching and its value is published
# to a PUBLIC body. Match from the credential WORD onward with no leading run,
# exactly as the canonical scrubber's literal-assignment rule does: the search
# is unanchored, so a prefixed name still matches from its credential word
# (the prefix simply stays in the output), there is one match start per
# credential word, and there is no length at which coverage stops. Only the
# trailing run is bounded, well above any real suffix.
_SECRET_KEY_WORD = (
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|PASS|AUTH|CREDENTIAL|"
    # `[-_]PWD`, not bare `PWD`: the canonical scrubber spells it `_pwd` for
    # the same reason, so an ordinary working-directory variable in a log line
    # is not mistaken for a credential.
    r"API[-_]?KEY|[-_]KEY|[-_]PWD|COOKIE|SESS(?:ION)?[-_]?ID|_SESSION|CSRF|XSRF)"
)
_SECRET_KEY_NAME = rf"{_SECRET_KEY_WORD}[A-Za-z0-9_-]{{0,64}}"
# The block-scalar rule is line-anchored (one start per line), so it can carry
# a bounded leading run and still match an ordinary `DB_PASSWORD: |` header.
_SECRET_BLOCK_KEY_NAME = rf"[A-Za-z0-9_-]{{0,64}}{_SECRET_KEY_NAME}"
# A quoted value runs to its closing quote; an escaped quote inside it is part
# of the value, not the end of it, so the alternation must step over `\"`.
_SECRET_ASSIGN_RE = re.compile(
    rf"({_SECRET_KEY_NAME})([\"']?\s*[=:]\s*)"
    r"(?:(?:basic|bearer|digest|token)\s+)?"
    r"(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|\S+)",
    re.I,
)
# The block-scalar structure is shared with the canonical scrubber so the two
# cannot drift again; this module keeps its own key alphabet.
_SECRET_BLOCK_SCALAR_RE = build_block_scalar_re(_SECRET_BLOCK_KEY_NAME)
_BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.I)
# Bare credential literals by well-known prefix or shape (the classes the
# gitleaks default ruleset flags): Anthropic / OpenAI keys, GitHub tokens,
# Slack tokens, AWS access key ids, three-part JWTs, and the provider key
# prefixes the canonical redactor knows (Stripe, xAI, NVIDIA, Cerebras).
_CREDENTIAL_LITERAL_RE = re.compile(
    r"\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{20,}"
    r"|sk_(?:live|test)_[A-Za-z0-9]{6,}"
    r"|(?:xai|nvapi|csk)-[A-Za-z0-9_-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|xox[abpors]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
)
# PEM / OpenSSH / PGP private-key blocks span lines; a block cut off before
# its END line is redacted to the end of the text.
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
    r"(?:.*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|.*)",
    re.S,
)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
# App routes (/api/..., /v1/...) with any query or fragment, including
# dynamic segments ([id], :id, {id}). Not filesystem
# roots: lock-held comments name $REPO and $RUNNER_LOCK (/Users/..., /tmp/...,
# /home/...).
_ROUTE_RE = re.compile(
    r"(?<![\w.])(/(?!Users\b|home\b|tmp\b|private\b|var\b|opt\b)"
    r"[a-z][\w.-]*(?:(?:/[\w.:\[\]{}-]+)+(?:[?#]\S*)?|[?#]\S*))"
)
# Any scheme, not just http(s): database, cache and socket URIs carry
# credentials in their userinfo or query.
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
# The quota ladder's one operator URL. Its token is kept only when sentence
# punctuation alone follows the literal; any other suffix (path, query, host
# tail) redacts the whole token, which carries no scheme once matched.
_USAGE_TOKEN_RE = re.compile(
    r"(?<![\w.-])(?:https?://)?claude\.ai/settings/usage(\S*)", re.I
)
_USAGE_TAIL_OK_RE = re.compile(r"[).,;:!'\"\]>]*")
_FILE_LINE_RE = re.compile(
    r"\b[\w./-]+\.(?:py|ts|tsx|js|mjs|cjs|sh|go|rb|java|json|yml|yaml|toml|md):\d+\b"
)
# Operator / broker / demo account-looking tokens, not pids.
_ACCOUNT_RE = re.compile(
    r"\b(?:user\s+)?(?:radon)?(?:trader|operator)\d+\b"
    r"|\b(?:U|DU|(?-i:F)|account)[-_]?\d{5,}\b",
    re.I,
)
_LOG_POINTER_RE = re.compile(
    r"(?i)(?:log:\s*`[^`]+`\s*on the runner|on the runner|check the runner|"
    r"see the private run log[^.]*|private security run dir[^.]*|"
    r"private archive pointer[^.]*|findings \(if any\) are in the private[^.]*"
    r"|never posted here)"
)
_USAGE_KEEP = "claude.ai/settings/usage"


def format_body(
    discovered: str,
    done: str,
    next_step: str | None,
    *,
    ci_time_savings: str | None = None,
) -> str:
    nxt = (next_step or "").strip() or FIXED_NEXT
    done_text = done.strip()
    extra = (ci_time_savings or "").strip()
    if extra:
        done_text = f"{done_text}\n\n{extra}"
    return (
        f"**{HEADING_DISCOVERED}**\n{discovered.strip()}\n\n"
        f"**{HEADING_DONE}**\n{done_text}\n\n"
        f"**{HEADING_NEXT}**\n{nxt}\n"
    )


def no_run_yet_body() -> str:
    return format_body(NO_RUN_DISCOVERED, NO_RUN_DONE, NO_RUN_NEXT)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text or "").strip()


def sections_for_phase(phase: str, status: str, detail: str) -> tuple[str, str, str]:
    phase = (phase or "nightly").strip() or "nightly"
    status = (status or "UNKNOWN").strip() or "UNKNOWN"
    detail = _strip_fences(detail or "")
    detail = _LOG_POINTER_RE.sub("", detail).strip(" ;,.")

    if status == "OK" or status.startswith("OK "):
        discovered = f"Nothing went wrong this {phase} phase."
        done = detail or f"The {phase} phase completed."
        return discovered, done, FIXED_NEXT

    discovered = f"{phase} {status}."
    if detail:
        discovered = f"{discovered} {detail}".rstrip()
        if not discovered.endswith("."):
            discovered += "."
    done = NOTHING_DONE
    lowered = status.lower()
    if "quotas exhausted" in lowered:
        next_step = QUOTA_NEXT
    elif status.startswith("INCOMPLETE") or status.startswith("TRUNCATED"):
        next_step = RESUME_NEXT
    elif status.startswith("TIMEOUT"):
        next_step = TIMEOUT_NEXT
    elif status.startswith("REFUSED") or status.startswith("GROUND TRUTH"):
        next_step = detail or RETRY_NEXT
    elif status.startswith("CRASHED") or status.startswith("KILLED"):
        next_step = detail or RETRY_NEXT
    else:
        next_step = detail or RETRY_NEXT
    return discovered, done, next_step


def sanitize(text: str) -> str:
    """Public security issue body: keep the write-up, strip exploit material."""
    if not text:
        return text
    # Preserve the one operator URL the quota ladder names, then restore.
    text = _USAGE_TOKEN_RE.sub(
        lambda m: "\x00USAGE\x00" + m.group(1)
        if _USAGE_TAIL_OK_RE.fullmatch(m.group(1))
        else REDACTED,
        text,
    )
    text = _PRIVATE_KEY_BLOCK_RE.sub(REDACTED, text)
    text = _COOKIE_HEADER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _SECRET_BLOCK_SCALAR_RE.sub(
        lambda m: f"{m.group('indent')}{m.group('lead')}{m.group('key')}{REDACTED}\n",
        text,
    )
    text = _URL_RE.sub(REDACTED, text)
    text = _ROUTE_RE.sub(REDACTED, text)
    text = _FILE_LINE_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _CREDENTIAL_LITERAL_RE.sub(REDACTED, text)
    text = _SECRET_ASSIGN_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text
    )
    text = _EMAIL_RE.sub(REDACTED, text)
    text = _ACCOUNT_RE.sub(REDACTED, text)
    # Same execution-id shapes the publication gate refuses. URLs (including
    # CI run links) are already redacted above, so no exemption is needed.
    for _, pattern in EXECUTION_ID_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = text.replace("\x00USAGE\x00", _USAGE_KEEP)
    text = _LOG_POINTER_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def format_phase_comment(
    *,
    phase: str,
    status: str,
    detail: str = "",
    stamp: str = "",
    sanitize: bool = False,
) -> str:
    del stamp  # kept on the CLI so wrappers can pass it; not shown
    if sanitize:
        detail = sanitize_text(detail)
        status = sanitize_text(status)
        phase = sanitize_text(phase)
    discovered, done, next_step = sections_for_phase(phase, status, detail)
    body = format_body(discovered, done, next_step)
    if sanitize:
        # Headings must survive; sanitize the whole body then restore them if
        # a greedy pattern chewed a slash in a heading (it should not).
        body = sanitize_text(body)
        for heading in (HEADING_DISCOVERED, HEADING_DONE, HEADING_NEXT):
            marked = f"**{heading}**"
            if marked not in body:
                return format_body(discovered, done, next_step)
    return body if body.endswith("\n") else body + "\n"


# Alias so `sanitize=True` kwarg does not shadow the function in the signature
# at runtime when we call sanitize() from inside format_phase_comment.
def sanitize_text(text: str) -> str:
    return sanitize(text)


CI_TIME_HEADING = "CI build time"
CI_TIME_FORMULA = (
    "% change = (after - before) / before * 100 (negative = faster). "
    "Cite Actions runs; do not invent timings."
)
CI_TIME_TABLE_HEADER = "| Job | Before | After | % change |"
CI_TIME_TABLE_RULE = "|---|---|---|---|"
DEFAULT_REQUIRED_SAMPLES = 5
_PENDING_AFTER_STATUSES = frozenset({"VALIDATING", "INSUFFICIENT_SAMPLE"})


def _format_secs(value: float) -> str:
    if value.is_integer():
        return f"{int(value)}s"
    return f"{value:.1f}s"


def _format_pct(before: float, after: float) -> str:
    change = (after - before) / before * 100
    if abs(change) < 0.05:
        return "0.0%"
    return f"{change:+.1f}%"


def _row_before_secs(row: dict) -> float:
    raw = row.get("before_secs", row.get("before"))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("before must be > 0; do not invent a baseline") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError("before must be > 0; do not invent a baseline")
    return value


def format_ci_build_time_savings(rows: list[dict] | None) -> str:
    """Job | Before | After | % change for a time-saving #196 write-up.

    After times come from cited Actions runs. Missing or VALIDATING /
    INSUFFICIENT_SAMPLE after-samples stay pending; % change is TBD until
    N comparable samples. Never invent an after time.
    """
    if not rows:
        raise ValueError("at least one row is required")
    lines = [
        f"**{CI_TIME_HEADING}**",
        CI_TIME_FORMULA,
        "",
        CI_TIME_TABLE_HEADER,
        CI_TIME_TABLE_RULE,
    ]
    for row in rows:
        job = " ".join(str((row or {}).get("job") or "").split())
        if not job:
            raise ValueError("each row needs a job name")
        before = _row_before_secs(row)
        try:
            required = int(row.get("required_samples") or DEFAULT_REQUIRED_SAMPLES)
        except (TypeError, ValueError) as exc:
            raise ValueError("required_samples must be a positive integer") from exc
        if required < 1:
            required = DEFAULT_REQUIRED_SAMPLES
        status = str(row.get("after_status") or "").strip().upper()
        after_raw = row.get("after_secs", row.get("after"))
        pending = status in _PENDING_AFTER_STATUSES or after_raw in (None, "")
        if pending:
            after_cell = "pending"
            if status in _PENDING_AFTER_STATUSES:
                samples = row.get("after_samples")
                if samples is None or samples == "":
                    after_cell = f"pending ({status})"
                else:
                    after_cell = (
                        f"pending ({status}, {int(samples)}/{required} samples)"
                    )
            pct_cell = f"TBD until {required} samples"
        else:
            try:
                after_value = float(after_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "after must be a measured number of seconds"
                ) from exc
            if not math.isfinite(after_value) or after_value < 0:
                raise ValueError("after must be a measured number of seconds")
            after_cell = _format_secs(after_value)
            pct_cell = _format_pct(before, after_value)
        lines.append(
            f"| {job} | {_format_secs(before)} | {after_cell} | {pct_cell} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nightly_issue_format")
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("no-run-yet", help="Issue create body when no cycle has run")
    phase = sub.add_parser("phase", help="Per-phase comment body")
    phase.add_argument("--phase", required=True)
    phase.add_argument("--status", required=True)
    phase.add_argument("--detail", default="")
    phase.add_argument("--stamp", default="")
    phase.add_argument(
        "--sanitize",
        action="store_true",
        help="Security loop: strip routes, file:line, secrets, accounts",
    )
    savings = sub.add_parser(
        "ci-time-savings",
        help="CI build-time before/after/% table for a time-saving fix",
    )
    savings.add_argument(
        "--row",
        action="append",
        default=[],
        help=(
            "JSON object: job, before_secs, optional after_secs, "
            "after_status, after_samples, required_samples"
        ),
    )
    args = parser.parse_args(argv)
    if args.mode == "no-run-yet":
        sys.stdout.write(no_run_yet_body())
        return 0
    if args.mode == "ci-time-savings":
        rows: list[dict] = []
        for raw in args.row:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"invalid --row JSON: {exc}", file=sys.stderr)
                return 2
            if not isinstance(parsed, dict):
                print("each --row must be a JSON object", file=sys.stderr)
                return 2
            rows.append(parsed)
        try:
            sys.stdout.write(format_ci_build_time_savings(rows))
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0
    sys.stdout.write(
        format_phase_comment(
            phase=args.phase,
            status=args.status,
            detail=args.detail,
            stamp=args.stamp,
            sanitize=args.sanitize,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
