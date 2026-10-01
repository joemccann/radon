"""Operator hold: the durable flag that keeps Radon's IB Gateway logged out.

2026-09-25: the Gateway and the operator share one IBKR username, and IBC's
``ExistingSessionDetectedAction=primary`` reclaimed the session three times,
kicking the operator off interactivebrokers.com. The hold is the one state
every Gateway start path consults. For a hold, "unreadable" must never mean
"the Gateway may log in".
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import ib_operator_hold as hold  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_PATH", str(tmp_path / "hold.json"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_OWNER_UID", str(os.getuid()))


def _hold_file(tmp_path: Path) -> Path:
    return tmp_path / "hold.json"


def test_absent_file_is_not_held():
    assert hold.is_held() is False


def test_set_hold_then_clear_round_trips(tmp_path):
    hold.set_hold("operator web login", "ssh:test")
    assert hold.is_held() is True
    assert hold.hold_state()["reason"] == "operator web login"
    hold.clear_hold("ssh:test")
    assert hold.is_held() is False


def test_written_flag_uses_the_canonical_prefix_the_root_shim_reads(tmp_path):
    hold.set_hold("x", "ssh:test")
    assert _hold_file(tmp_path).read_text().startswith('{"held": true')
    hold.clear_hold("ssh:test")
    assert _hold_file(tmp_path).read_text().startswith(hold.NOT_HELD_PREFIX)


@pytest.mark.parametrize(
    "content",
    ["", "garbage", "[]", '{"held": "no"}', '{ "held": false }', '{"reason": "x"}'],
)
def test_anything_but_the_canonical_not_held_flag_is_held(tmp_path, content):
    _hold_file(tmp_path).write_text(content)
    assert hold.is_held() is True


def test_symlinked_flag_is_held(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(hold.NOT_HELD_PREFIX + "}")
    _hold_file(tmp_path).symlink_to(target)
    assert hold.is_held() is True


def test_flag_owned_by_the_wrong_user_is_held(tmp_path, monkeypatch):
    _hold_file(tmp_path).write_text(hold.NOT_HELD_PREFIX + "}")
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_OWNER_UID", str(os.getuid() + 1))
    assert hold.is_held() is True


def test_flag_is_world_readable_so_radon_daemons_can_honor_it(tmp_path):
    hold.set_hold("x", "ssh:test")
    assert _hold_file(tmp_path).stat().st_mode & 0o777 == 0o644


def test_every_change_is_audited(tmp_path):
    hold.set_hold("operator web login", "ssh:test")
    hold.clear_hold("ssh:test")
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["hold", "clear"]
    assert all(event["actor"] == "ssh:test" for event in events)


def test_an_unwritable_audit_log_does_not_block_the_hold(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_AUDIT", str(tmp_path / "missing-dir" / "x" / "audit.jsonl"))
    (tmp_path / "missing-dir").write_text("a file, not a directory")
    hold.set_hold("x", "ssh:test")
    assert hold.is_held() is True


def test_cli_status_exits_held_rc(tmp_path):
    assert hold.main(["status"]) == 0
    hold.set_hold("x", "ssh:test")
    assert hold.main(["status"]) == hold.HELD_RC


def test_cli_hold_and_clear(tmp_path):
    assert hold.main(["hold", "--reason", "drill", "--actor", "ssh:test"]) == 0
    assert hold.is_held() is True
    assert hold.main(["clear", "--actor", "ssh:test"]) == 0
    assert hold.is_held() is False
