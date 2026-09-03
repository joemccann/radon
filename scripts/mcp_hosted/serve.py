"""Entrypoint for radon-mcp.service: python -m scripts.mcp_hosted.serve

REL-193 (R-526, R-552): the process serves the streamable-HTTP app behind an
in-process body cap and a uvicorn concurrency bound, so a caller that reaches
8334 directly (or a Caddy config regression) still cannot OOM the 512M unit
or saturate it with queued anonymous requests.
"""
from __future__ import annotations

import uvicorn

from scripts.mcp_hosted.server import build_app, mcp

if __name__ == "__main__":
    # Mirrors FastMCP.run_streamable_http_async, but serves the bounded app.
    uvicorn.run(
        build_app(),
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
        limit_concurrency=64,
    )
