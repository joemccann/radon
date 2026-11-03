"""The xAI bearer token must never appear in curl argv (process table)."""
import json
from unittest.mock import patch

from model_gateway import _x_search_backend as fetch_x_xai


class _Result:
    returncode = 0
    stdout = json.dumps({"output": []})
    stderr = ""


class TestTokenNeverInArgv:
    def test_header_goes_over_stdin_not_argv(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["input"] = kwargs.get("input")
            return _Result()

        with patch.object(fetch_x_xai, "_xai_token", return_value="sk-test-sekrit"), \
                patch("subprocess.run", side_effect=fake_run):
            fetch_x_xai.xai_search("someaccount", days=1)

        assert all("sk-test-sekrit" not in arg for arg in captured["cmd"])
        assert "--config" in captured["cmd"]
        assert "sk-test-sekrit" in captured["input"]


def test_generated_x_reasons_require_humanizer_review():
    captured = {}
    def fake_run(cmd, **kwargs):
        captured["payload"] = json.loads(cmd[cmd.index("-d") + 1])
        return _Result()
    with patch.object(fetch_x_xai, "_xai_token", return_value="test-grant"), \
            patch("subprocess.run", side_effect=fake_run):
        fetch_x_xai.xai_search("someaccount", days=1)
    instructions = " ".join(item["content"] for item in captured["payload"]["input"])
    assert "Humanizer" in instructions
    assert "90" in instructions
    assert "source uncertainty" in instructions
    assert "negative parallelism" in instructions
    assert "quoted source text" in instructions
