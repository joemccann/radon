"""Test processes must never inherit notification credentials or deliver pages."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _probe(tmp_path, source):
    """Use the real ancestor conftests in a small, isolated pytest process."""
    (tmp_path / "conftest.py").write_text((ROOT / "conftest.py").read_text())
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "conftest.py").write_text(
        (ROOT / "scripts/conftest.py").read_text()
    )
    for subtree in ("tests", "scripts/api/tests", "cloud/tests"):
        directory = tmp_path / subtree
        directory.mkdir(parents=True)
        (directory / "test_probe.py").write_text(source)
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\naddopts = --import-mode=importlib\n")
    env = dict(os.environ)
    env.update(
        PUSHOVER_USER="inherited-canary-user",
        PUSHOVER_TOKEN="inherited-canary-token",
        PYTHONPATH=str(ROOT / "scripts"),
        PYTEST_ADDOPTS="",
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(config), "-q", str(tmp_path)],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60,
    )


def test_all_subtrees_strip_inherited_and_dotenv_pushover_credentials(tmp_path):
    result = _probe(tmp_path, '''
import os
import dotenv

def test_credentials(tmp_path):
    assert "PUSHOVER_USER" not in os.environ
    assert "PUSHOVER_TOKEN" not in os.environ
    path = tmp_path / ".env"
    path.write_text("PUSHOVER_USER=dotenv-canary\\nPUSHOVER_TOKEN=dotenv-canary\\n")
    dotenv.load_dotenv(path)
    assert "PUSHOVER_USER" not in os.environ
    assert "PUSHOVER_TOKEN" not in os.environ
''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout


@pytest.mark.parametrize("client", ["urllib", "requests"])
@pytest.mark.parametrize("endpoint", [
    "messages.json", "receipts/cancel_by_tag/test.json",
])
def test_live_transports_fail_even_if_caller_swallows_error(tmp_path, client, endpoint):
    # The RED revision is safe too: fake the last boundary before attempting
    # delivery. A missing guard reaches the sentinel, never a real server.
    result = _probe(tmp_path, f'''
import http.client
import os
import urllib.request
import requests

def test_transport(monkeypatch):
    def unsafe(*args, **kwargs):
        raise RuntimeError("UNSAFE TRANSPORT REACHED")
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", unsafe)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", unsafe)
    monkeypatch.setenv("PUSHOVER_USER", "synthetic-user")
    monkeypatch.setenv("PUSHOVER_TOKEN", "synthetic-token")
    url = "https://api.pushover.net/1/{endpoint}"
    try:
        if "{client}" == "urllib":
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
            opener.open(urllib.request.Request(url, data=b"synthetic=only"))
        else:
            requests.post(url, data={{"synthetic": "only"}})
    except Exception as exc:
        assert "UNSAFE TRANSPORT REACHED" not in str(exc)
    # Swallowing a transport exception must still fail fixture teardown.
''')
    assert result.returncode == 1, result.stdout + result.stderr
    assert "UNSAFE TRANSPORT REACHED" not in result.stdout + result.stderr
    assert "3 passed, 3 errors" in result.stdout, result.stdout + result.stderr
    assert "Pushover network delivery is forbidden in tests" in result.stdout
