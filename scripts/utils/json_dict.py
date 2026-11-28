"""Read a JSON object from disk.

Missing, unreadable, and non-object files are absence. Callers that need
a checksum or a different shape use their own reader.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def read_json_dict(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
