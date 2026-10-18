"""Footer shared by every generated X report's copy."""

X_SHARE_FOOTER = "@grok explain this for normies"


def with_x_share_footer(text: str) -> str:
    body = text.rstrip()
    if body == X_SHARE_FOOTER or body.endswith("\n" + X_SHARE_FOOTER):
        return body
    return "\n\n".join(filter(None, (body, X_SHARE_FOOTER)))
