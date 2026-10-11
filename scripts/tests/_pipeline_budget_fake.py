"""Shared wire-level fake for Turso /v2/pipeline round-trip budget tests."""
from __future__ import annotations

import io
import json


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _cell(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


class PipelineRecorder:
    """Stands in for ``urllib.request.urlopen``; answers every execute
    request in a pipeline with ``rows_for(sql)`` and records each POST."""

    def __init__(self, rows_for):
        self.rows_for = rows_for
        self.posts: list[dict] = []

    def __call__(self, req, timeout=None):
        assert req.full_url == "https://example.turso.io/v2/pipeline"
        body = json.loads(req.data.decode("utf-8"))
        self.posts.append(body)
        results = []
        for item in body["requests"]:
            if item["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            rows = self.rows_for(item["stmt"]["sql"])
            results.append({
                "type": "ok",
                "response": {"type": "execute", "result": {
                    "cols": [],
                    "rows": [
                        [_cell(v) for v in row]
                        for row in rows
                    ],
                }},
            })
        return _Resp(json.dumps({"results": results}).encode("utf-8"))
