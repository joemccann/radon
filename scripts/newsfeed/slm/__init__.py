"""Newsfeed text-tagger SLM v1.

Radon SLM tagger: internal specialist, not a SotA replacement for open research.
"""

from newsfeed.slm.contract import (
    BODY_CHAR_LIMIT,
    HONESTY_LABEL,
    SLM_BASE_ID,
    SLM_JSON_SCHEMA,
    SLM_TAGGER_NAME,
    SLM_TAGGER_RUNG,
    tagger_system_prompt,
)

__all__ = [
    "BODY_CHAR_LIMIT",
    "HONESTY_LABEL",
    "SLM_BASE_ID",
    "SLM_JSON_SCHEMA",
    "SLM_TAGGER_NAME",
    "SLM_TAGGER_RUNG",
    "tagger_system_prompt",
]
