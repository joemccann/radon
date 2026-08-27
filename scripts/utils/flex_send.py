"""Gate for Flex Web Service SendRequest. File ingest is the default.

Weekday and page-driven callers must not SendRequest. Sunday recon is
opt-in via ``allowed=True`` after ``raise_if_blocked()``.
"""

from __future__ import annotations


class FlexSendDisabled(RuntimeError):
    """Live Flex fetch is off. Use --from-file."""


def assert_sendrequest_permitted(*, allowed: bool) -> None:
    from utils.flex_embargo import raise_if_blocked

    raise_if_blocked()
    if not allowed:
        raise FlexSendDisabled(
            "Flex Web Service is file-ingest only. "
            "Pass --from-file or --sendrequest (Sunday recon, after embargo)."
        )
