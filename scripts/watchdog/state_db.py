"""Local, bounded persistence for watchdog control state.

Alert hysteresis, cooldowns, and operator acknowledgements are host-local
control-plane state. Keeping them in remote Turso lets a data-plane outage
disable the component responsible for reporting that outage.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "watchdog_state.db"


def get_state_db() -> sqlite3.Connection:
    path = Path(os.environ.get("RADON_WATCHDOG_STATE_DB", DEFAULT_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=2.0)
    db.execute("PRAGMA busy_timeout=2000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_cooldowns (
          service TEXT NOT NULL,
          kind TEXT NOT NULL,
          consecutive_failures INTEGER NOT NULL DEFAULT 0,
          last_notified_at TEXT,
          last_outcome TEXT,
          PRIMARY KEY (service, kind)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_acks (
          service TEXT PRIMARY KEY,
          acked_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          reason TEXT
        )
        """
    )
    db.commit()
    return db
