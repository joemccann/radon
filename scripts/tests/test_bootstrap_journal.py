#!/usr/bin/env python3
"""Tests for the retired journal bootstrap entry point."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


def test_bootstrap_journal_is_retired(capsys):
    import db.bootstrap_journal as mod

    assert mod.main() == 0
    assert "retired" in capsys.readouterr().out
