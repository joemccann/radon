"""Hosted Streamable HTTP MCP server (issue #232 chunk 1).

A DEDICATED process — never a mount on scripts/api/server.py — served by
radon-mcp.service on 127.0.0.1:8334 behind Caddy's `handle /mcp*` on
app.radon.run, so an anonymous MCP caller can never reach the FastAPI
/docs or operator /openapi.json surface. The local radon-kb stdio server
(scripts/knowledge/mcp_server.py) is a separate, checkout-only surface;
none of its kb_* tools are registered here.
"""
