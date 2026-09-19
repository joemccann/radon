"""The xAI Bearer token must never appear on the curl argv (ps-visible)."""

from __future__ import annotations

import json
import subprocess

from scripts import fetch_x_xai


def test_bearer_token_travels_via_stdin_config_not_argv(monkeypatch):
    monkeypatch.setattr(fetch_x_xai, "_xai_token", lambda: "xai-secret-token")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")

        class R:
            returncode = 0
            stdout = json.dumps({"output": []})
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    fetch_x_xai.xai_search("someaccount", days=1)
    assert not any("xai-secret-token" in part for part in seen["cmd"])
    assert "--config" in seen["cmd"]
    assert 'Authorization: Bearer xai-secret-token' in (seen["input"] or "")
