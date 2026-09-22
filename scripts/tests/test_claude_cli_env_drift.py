"""Claude Code is not pinned (2026-09-19), so a new CLI can add an env var that
reroutes model billing off the claude.ai subscription. The drift check diffs
the installed binary's env names against the reviewed list and pages once per
CLI version when an unreviewed name appears."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("drift", REPO / "scripts" / "claude_cli_env_drift.py")
drift = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drift)


def _setup(tmp_path: Path, binary_names: list[str], reviewed: list[str]):
    binary = tmp_path / "2.9.999"
    binary.write_bytes(b"\x00junk".join(n.encode() for n in binary_names) + b"\x00lowercase_ANTHROPIC_x")
    reviewed_file = tmp_path / "reviewed.txt"
    reviewed_file.write_text("# comment\n" + "\n".join(reviewed) + "\n")
    return binary, reviewed_file


def test_extracts_only_the_three_prefixes(tmp_path):
    binary, _ = _setup(tmp_path, ["ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_X", "AWS_BEARER_TOKEN_Y", "OTHER_VAR"], [])
    assert drift.env_names(binary) == {"ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_X", "AWS_BEARER_TOKEN_Y"}


def test_reviewed_list_ignores_comments(tmp_path):
    _, reviewed = _setup(tmp_path, [], ["ANTHROPIC_API_KEY"])
    assert drift.reviewed_names(reviewed) == {"ANTHROPIC_API_KEY"}


def test_no_drift_exits_zero_and_never_pages(tmp_path, capsys):
    binary, reviewed = _setup(tmp_path, ["ANTHROPIC_API_KEY"], ["ANTHROPIC_API_KEY"])
    sent = []
    rc = drift.main(["--binary", str(binary), "--reviewed", str(reviewed), "--state-dir", str(tmp_path), "--notify"],
                    send=lambda t, m: sent.append((t, m)) or True)
    assert rc == 0 and sent == []


def test_new_name_exits_one_names_it_and_pages_once_per_version(tmp_path, capsys):
    binary, reviewed = _setup(tmp_path, ["ANTHROPIC_API_KEY", "CLAUDE_CODE_NEW_BEARER"], ["ANTHROPIC_API_KEY"])
    sent = []
    args = ["--binary", str(binary), "--reviewed", str(reviewed), "--state-dir", str(tmp_path), "--notify"]
    assert drift.main(args, send=lambda t, m: sent.append((t, m)) or True) == 1
    assert "CLAUDE_CODE_NEW_BEARER" in capsys.readouterr().out
    assert len(sent) == 1 and "2.9.999" in sent[0][1] and "CLAUDE_CODE_NEW_BEARER" in sent[0][1]
    # Same version next night: still exits 1, no second page.
    assert drift.main(args, send=lambda t, m: sent.append((t, m)) or True) == 1
    assert len(sent) == 1


def test_failed_page_is_retried_next_run(tmp_path):
    binary, reviewed = _setup(tmp_path, ["CLAUDE_CODE_NEW"], [])
    args = ["--binary", str(binary), "--reviewed", str(reviewed), "--state-dir", str(tmp_path), "--notify"]
    assert drift.main(args, send=lambda t, m: False) == 1
    sent = []
    drift.main(args, send=lambda t, m: sent.append(m) or True)
    assert len(sent) == 1


def test_committed_baseline_covers_the_wrapper_reroute_lists():
    reviewed = drift.reviewed_names(REPO / "scripts" / "claude_cli_env_reviewed.txt")
    text = (REPO / "scripts" / "reliability_weekend.sh").read_text()
    live = [n for n in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK") if n in text]
    assert live and set(live) <= reviewed
