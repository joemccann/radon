"""Optional LLM-written CTA share copy, behind a numeric firewall.

Deterministic templates cannot say anything the payload does not contain, but
they also read the same on every session inside a regime. A model writes
livelier copy and reintroduces the exact hazard that produced the 2026-08-07
post: a confident, entirely invented "$90.6B forced selling pipeline".

So the model is not trusted with arithmetic. It is handed an explicit fact
sheet plus the deterministic draft, told it may not introduce a figure that is
not in them, and then CHECKED: every number in its output must trace back to
that fact set (rounding-aware, so 3.7 for 3.68 passes and 90.6 does not). Copy
that fails is discarded and the deterministic template ships instead.

OFF unless RADON_CTA_LLM_COPY is set. A missing key, missing SDK, timeout, or
API error degrades to the template. The share is never blocked.
"""

from __future__ import annotations

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional, Sequence

PROJECT_DIR = Path(__file__).resolve().parents[2]
# web/.env is where ANTHROPIC_API_KEY lives; the root .env is the fallback.
ENV_FILES = (PROJECT_DIR / "web" / ".env", PROJECT_DIR / ".env")

ENABLE_ENV = "RADON_CTA_LLM_COPY"
MODEL_ENV = "RADON_CTA_LLM_MODEL"
DEFAULT_MODEL = "claude-opus-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"

REQUEST_TIMEOUT_SECONDS = 45.0
MAX_TOKENS = 4000
MAX_COPY_CHARS = 1500

_TRUTHY = {"1", "true", "yes", "on"}
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

SYSTEM_PROMPT = """You write the X posts for Radon, a market-structure research desk.

Absolute rules:
1. Every number, percentage, and dollar figure in your post MUST appear in the
   FACTS or the DRAFT you are given. Never introduce a figure of your own,
   never estimate one, and never restate one at a different value.
2. Keep the direction of the story exactly as the FACTS state it. A maximum
   LONG deleverages into weakness; a maximum SHORT covers into strength.
3. No em dashes. No hashtags. No links other than the closing line.
4. Plain text, short paragraphs, under 1200 characters.
5. End with this exact line: Analyzed by Radon · radon.run

If a fact is not in FACTS, it does not go in the post."""


@lru_cache(maxsize=1)
def _project_env() -> dict:
    """Values from the project .env files, read without touching os.environ."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    merged: dict = {}
    for path in reversed(ENV_FILES):
        if path.exists():
            merged.update({k: v for k, v in dotenv_values(path).items() if v})
    return merged


def setting(env: dict, name: str) -> str:
    """One setting, process environment first.

    The project .env files are consulted ONLY when reading the real process
    environment, so a test (or any caller) that passes an explicit mapping gets
    exactly what it passed and can never reach the operator's live credentials.
    """
    value = (env.get(name) or "").strip()
    if value or env is not os.environ:
        return value
    return (_project_env().get(name) or "").strip()


def llm_copy_enabled(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    return setting(env, ENABLE_ENV).lower() in _TRUTHY


def model_name(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    return setting(env, MODEL_ENV) or DEFAULT_MODEL


# ── Numeric firewall ──────────────────────────────────────────────────────────

def _as_float(token: str) -> Optional[float]:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _decimals(token: str) -> int:
    _, _, frac = token.partition(".")
    return len(frac)


def collect_numbers(*sources: object) -> list:
    """Every figure the copy is allowed to use, from facts and draft alike.

    Strings are mined too, so a date or an already-formatted "+3.69" in the
    deterministic draft counts as supported. Signs are dropped: prose renders
    direction in words as often as in symbols.
    """
    allowed: list = []

    def walk(node: object) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            allowed.append(abs(float(node)))
        elif isinstance(node, str):
            for token in _NUMBER_RE.findall(node):
                value = _as_float(token)
                if value is not None:
                    allowed.append(abs(value))
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                walk(item)

    for source in sources:
        walk(source)
    return allowed


def unsupported_numbers(text: str, allowed: Sequence[float]) -> list:
    """Figures in `text` that no allowed value rounds to at that precision."""
    offenders = []
    for token in _NUMBER_RE.findall(text or ""):
        value = _as_float(token)
        if value is None:
            continue
        places = _decimals(token)
        value = round(abs(value), places)
        if not any(round(candidate, places) == value for candidate in allowed):
            offenders.append(token)
    return offenders


def copy_problems(text: str, allowed: Sequence[float]) -> list:
    """Every reason this copy must not be published, or an empty list."""
    problems = []
    body = (text or "").strip()

    if not body:
        problems.append("empty response")
    if len(body) > MAX_COPY_CHARS:
        problems.append(f"{len(body)} chars exceeds the {MAX_COPY_CHARS} cap")
    if "—" in body:
        problems.append("contains an em dash")

    offenders = unsupported_numbers(body, allowed)
    if offenders:
        problems.append(f"unverified figures: {', '.join(offenders)}")

    return problems


# ── Model call ────────────────────────────────────────────────────────────────

def build_prompt(facts: dict, draft: str) -> str:
    return (
        "FACTS (the only figures you may use):\n"
        f"{json.dumps(facts, indent=2, default=str)}\n\n"
        "DRAFT (deterministic; correct but formulaic):\n"
        f"{draft}\n\n"
        "Rewrite the draft as a distinct post for today. Keep every figure and "
        "the direction of the story. Lead with what changed since the prior "
        "session where the facts describe a change."
    )


def _anthropic_caller(*, system: str, user: str, model: str, env: dict) -> str:
    api_key = setting(env, API_KEY_ENV)
    if not api_key:
        raise RuntimeError(f"{API_KEY_ENV} is not set")

    from anthropic import Anthropic  # lazy: the deterministic path must not need it

    client = Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1)
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )


def generate_share_copy(
    facts: dict,
    fallback: str,
    *,
    env: Optional[dict] = None,
    caller: Optional[Callable[..., str]] = None,
) -> str:
    """LLM-written copy when it verifies, the deterministic template otherwise."""
    env = os.environ if env is None else env
    if not llm_copy_enabled(env):
        return fallback

    try:
        text = (caller or _anthropic_caller)(
            system=SYSTEM_PROMPT,
            user=build_prompt(facts, fallback),
            model=model_name(env),
            env=env,
        )
    except Exception as exc:  # noqa: BLE001 — no LLM failure may block a share
        print(f"[cta-share] LLM copy unavailable ({exc}); using template", file=sys.stderr)
        return fallback

    problems = copy_problems(text, collect_numbers(facts, fallback))
    if problems:
        print(
            f"[cta-share] LLM copy rejected ({'; '.join(problems)}); using template",
            file=sys.stderr,
        )
        return fallback

    return text.strip()
