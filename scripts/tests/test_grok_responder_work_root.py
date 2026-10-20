"""The isolated responder executes a root-owned tree and edits a separate clone."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_page_responder as responder  # noqa: E402


def test_unset_work_root_stays_the_file_project(monkeypatch):
    monkeypatch.delenv("RADON_GROK_REPO", raising=False)
    assert responder.work_root() == responder._PROJECT_DIR


def test_absolute_work_root_is_the_clone_not_the_executed_tree(monkeypatch, tmp_path):
    clone = tmp_path / "repo"
    monkeypatch.setenv("RADON_GROK_REPO", str(clone))
    assert responder.work_root() == clone
    assert responder.work_root() != responder._PROJECT_DIR


def test_relative_work_root_is_refused(monkeypatch):
    monkeypatch.setenv("RADON_GROK_REPO", "repo")
    with pytest.raises(SystemExit) as raised:
        responder.work_root()
    assert raised.value.code == 2
