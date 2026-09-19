"""The xAI bearer token must never appear in curl argv (process table)."""
import json
from unittest.mock import patch

import fetch_x_xai


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
        assert "@-" in captured["cmd"]
        assert captured["input"] == "Authorization: Bearer sk-test-sekrit"
