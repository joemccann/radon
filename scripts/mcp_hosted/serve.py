"""Entrypoint for radon-mcp.service: python -m scripts.mcp_hosted.serve

REL-193 (R-526, R-552): the process serves the streamable-HTTP app behind an
in-process body cap and a uvicorn concurrency bound, so a caller that reaches
8334 directly (or a Caddy config regression) still cannot OOM the 512M unit
or saturate it with queued anonymous requests.
"""
from __future__ import annotations

from scripts.mcp_hosted.server import MAX_REQUEST_BODY_BYTES, mcp

# 503s excess load instead of queueing it in memory. Well above the ~40-thread
# sync-tool pool so legitimate bursts are not clipped.
LIMIT_CONCURRENCY = 64


class BodyLimitMiddleware:
    """Reject any request whose body exceeds MAX_REQUEST_BODY_BYTES with 413.

    Fast path: a declared Content-Length over the cap never reads a byte.
    Slow path: chunked/lying senders are counted as chunks arrive and cut off
    at the bound — the full body is never buffered.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        declared = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                break
        if declared is not None and declared > MAX_REQUEST_BODY_BYTES:
            await _send_413(send)
            return

        received = 0
        tripped = False

        async def bounded_receive():
            nonlocal received, tripped
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_REQUEST_BODY_BYTES:
                    tripped = True
                    # Present a terminal disconnect to the inner app; the
                    # outer wrapper answers 413.
                    return {"type": "http.disconnect"}
            return message

        sent_started = False

        async def guarded_send(message):
            nonlocal sent_started
            if tripped:
                return
            if message.get("type") == "http.response.start":
                sent_started = True
            await send(message)

        await self.app(scope, bounded_receive, guarded_send)
        if tripped and not sent_started:
            await _send_413(send)


async def _send_413(send):
    body = b'{"error": "request body too large"}'
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def build_app():
    """The bounded ASGI app radon-mcp.service actually serves."""
    return BodyLimitMiddleware(mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        build_app(),
        host=mcp.settings.host,
        port=mcp.settings.port,
        limit_concurrency=LIMIT_CONCURRENCY,
    )
