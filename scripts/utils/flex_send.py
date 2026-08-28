"""Gate for Flex Web Service SendRequest. File ingest is the default.

Weekday and page-driven callers must not SendRequest. Sunday recon is
opt-in via ``allowed=True`` after ``raise_if_blocked()``.
"""

from __future__ import annotations


class FlexSendDisabled(RuntimeError):
    """Live Flex fetch is off. Use --from-file."""


def assert_sendrequest_permitted(*, allowed: bool) -> None:
    # `allowed` FIRST. Embargo state describes what a SendRequest would do to
    # the token, so it can only be relevant to a run that will issue one. The
    # daily cash_flow_sync run passes neither --from-file nor --sendrequest and
    # touches no network, and checking the embargo ahead of `allowed` made it
    # alarm or not purely as a function of sidecar state. R-360.
    if not allowed:
        raise FlexSendDisabled(
            "Flex Web Service is file-ingest only. "
            "Pass --from-file or --sendrequest (Sunday recon, after embargo)."
        )
    from utils.flex_embargo import raise_if_blocked

    raise_if_blocked()
