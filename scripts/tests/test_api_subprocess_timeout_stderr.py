"""A killed script must leave evidence of where it was when the kill landed.

ib_sync.py timed out 162 times in 15.5h on 2026-10-08 at the 30s kill, and
journalctl held only "Script ib_sync.py timed out after 30s": the child's
stderr (its phase marks and warnings) was buffered inside communicate() and
discarded with the cancelled read. run_script now logs the stderr tail it
had collected when the timeout fired.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import api.subprocess as api_subprocess  # noqa: E402


def test_timeout_logs_the_childs_stderr_tail(tmp_path, monkeypatch, caplog):
    (tmp_path / "slow_sync.py").write_text(
        "import sys, time\n"
        "print('{\"phase\":\"connect\",\"elapsed_s\":0.4}', file=sys.stderr, flush=True)\n"
        "print('stuck in option history', file=sys.stderr, flush=True)\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(api_subprocess, "SCRIPTS_DIR", tmp_path)

    with caplog.at_level(logging.ERROR, logger=api_subprocess.logger.name):
        result = asyncio.run(api_subprocess.run_script("slow_sync.py", timeout=1.5))

    assert not result.ok
    assert result.error == "Script timed out after 1.5s"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "slow_sync.py timed out" in logged
    assert "stuck in option history" in logged
    assert '"phase":"connect"' in logged


def test_success_still_parses_stdout_and_ignores_stderr(tmp_path, monkeypatch):
    (tmp_path / "ok_sync.py").write_text(
        "import json, sys\n"
        "print('progress', file=sys.stderr)\n"
        "print(json.dumps({'status': 'ok'}))\n"
    )
    monkeypatch.setattr(api_subprocess, "SCRIPTS_DIR", tmp_path)

    result = asyncio.run(api_subprocess.run_script("ok_sync.py", timeout=10))

    assert result.ok
    assert result.data == {"status": "ok"}
