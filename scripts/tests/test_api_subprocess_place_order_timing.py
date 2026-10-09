"""Successful ib_place_order.py must journal its ib_hot_path_timing stderr line.

Stdout stays result JSON. The timing record is already on stderr and is
dropped on the success return. Log that one line, for this script only.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import api.subprocess as api_subprocess  # noqa: E402

_TIMING_LINE = (
    '{"event":"ib_hot_path_timing","job":"ib_place_order","total_s":1.25}'
)
_NOISE = ("qualifying contract", "disconnecting")


def _run_leaving_a_current_loop(coro):
    """asyncio.run() clears the thread's current loop; later tests that call
    asyncio.get_event_loop() would then raise. Run on a private loop and
    install a fresh one as current afterwards."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _write_script(tmp_path: Path, name: str, code: int) -> None:
    (tmp_path / name).write_text(
        "import json, sys\n"
        "print('qualifying contract', file=sys.stderr)\n"
        f"print({_TIMING_LINE!r}, file=sys.stderr)\n"
        "print('disconnecting', file=sys.stderr)\n"
        "print(json.dumps({'status': 'ok', 'orderId': 7}))\n"
        f"raise SystemExit({code})\n"
    )


def _info_messages(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "radon.subprocess" and record.levelno == logging.INFO
    ]


def test_place_order_hot_path_timing_logged_on_success(tmp_path, monkeypatch, caplog):
    _write_script(tmp_path, "ib_place_order.py", 0)
    monkeypatch.setattr(api_subprocess, "SCRIPTS_DIR", tmp_path)

    with caplog.at_level(logging.INFO, logger="radon.subprocess"):
        result = _run_leaving_a_current_loop(
            api_subprocess.run_script("ib_place_order.py", timeout=10)
        )

    assert result.ok
    assert result.data == {"status": "ok", "orderId": 7}
    messages = _info_messages(caplog)
    assert messages == [_TIMING_LINE]
    for noise in _NOISE:
        assert noise not in messages[0]


def test_place_order_hot_path_timing_not_logged_for_other_script(
    tmp_path, monkeypatch, caplog
):
    _write_script(tmp_path, "ib_sync.py", 0)
    monkeypatch.setattr(api_subprocess, "SCRIPTS_DIR", tmp_path)

    with caplog.at_level(logging.INFO, logger="radon.subprocess"):
        result = _run_leaving_a_current_loop(
            api_subprocess.run_script("ib_sync.py", timeout=10)
        )

    assert result.ok
    assert result.data == {"status": "ok", "orderId": 7}
    assert _info_messages(caplog) == []
    assert not any("ib_hot_path_timing" in record.getMessage() for record in caplog.records)


def test_place_order_hot_path_timing_not_logged_on_failure(tmp_path, monkeypatch, caplog):
    _write_script(tmp_path, "ib_place_order.py", 1)
    monkeypatch.setattr(api_subprocess, "SCRIPTS_DIR", tmp_path)

    with caplog.at_level(logging.INFO, logger="radon.subprocess"):
        result = _run_leaving_a_current_loop(
            api_subprocess.run_script("ib_place_order.py", timeout=10)
        )

    assert not result.ok
    assert result.exit_code == 1
    assert _info_messages(caplog) == []
    assert any(
        record.levelno == logging.WARNING and record.name == "radon.subprocess"
        for record in caplog.records
    )
