#!/usr/bin/env python3
"""
Preset loader — strategy-agnostic ticker/pair presets.

Used by leap-scan, garch-convergence, discover, and any future strategy.

Usage:
    from utils.presets import load_preset, list_presets, Preset

    # List all
    for name, desc in list_presets():
        print(f"{name}: {desc}")

    # Load
    p = load_preset("sp500-semis")
    p.tickers  # ["NVDA", "AMD", ...]
    p.pairs    # [["NVDA", "AMD"], ...]
    p.name     # "sp500-semis"
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Dict, Optional


PRESETS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "presets"


@dataclass
class Preset:
    """A strategy-agnostic ticker preset."""
    name: str
    description: str
    tickers: List[str]
    pairs: List[List[str]] = field(default_factory=list)
    sector: str = ""
    sub_industry: str = ""
    vol_driver: str = ""
    source: str = ""
    groups: Dict = field(default_factory=dict)

    @property
    def ticker_count(self) -> int:
        return len(self.tickers)

    @property
    def pair_count(self) -> int:
        return len(self.pairs)

    def group_names(self) -> List[str]:
        """Return list of group keys (for sp500 master preset)."""
        return list(self.groups.keys())

    def group_tickers(self, group_key: str) -> List[str]:
        """Return tickers for a specific group."""
        if group_key in self.groups:
            return self.groups[group_key].get("tickers", [])
        return []

    def group_pairs(self, group_key: str) -> List[List[str]]:
        """Return pairs for a specific group."""
        if group_key in self.groups:
            return self.groups[group_key].get("pairs", [])
        return []


def list_presets() -> List[Tuple[str, str, int]]:
    """
    List all available presets.
    
    Returns:
        List of (name, description, ticker_count) tuples, sorted by name.
    """
    results = []
    if not PRESETS_DIR.exists():
        return results

    for f in sorted(PRESETS_DIR.glob("*.json")):
        try:
            with open(f) as fh:
                data = json.load(fh)
            name = f.stem
            desc = data.get("description", "")
            count = len(data.get("tickers", []))
            results.append((name, desc, count))
        except (json.JSONDecodeError, KeyError):
            continue

    return results


import re

# Preset names are bare slugs — the only thing that ever legitimately reaches
# load_preset. Anchoring on this before the path join closes the traversal
# primitive that four authenticated scan endpoints (leap/garch/theta/strength)
# forward the client `preset` string into unsanitized.
_PRESET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _resolve_preset_path(name: str) -> Path:
    """Map a preset name to its file, rejecting traversal/absolute inputs.

    Raises FileNotFoundError (never returns an out-of-dir path) so callers
    surface the same "not found" contract for both a bogus slug and a
    traversal attempt — no information leak about the filesystem layout.
    """
    if not _PRESET_NAME_RE.match(name):
        raise FileNotFoundError(f"Preset '{name}' not found.")
    candidate = (PRESETS_DIR / f"{name}.json").resolve()
    if candidate.parent != PRESETS_DIR.resolve():
        raise FileNotFoundError(f"Preset '{name}' not found.")
    return candidate


def load_preset(name: str) -> Preset:
    """
    Load a preset by name.

    Args:
        name: Preset name (e.g., "sp500-semis"). ".json" extension optional.

    Returns:
        Preset dataclass instance.

    Raises:
        FileNotFoundError: If preset doesn't exist or the name is not a bare slug.
        ValueError: If preset file is malformed.
    """
    # Strip .json if provided
    name = name.replace(".json", "")

    filepath = _resolve_preset_path(name)
    if not filepath.exists():
        available = [f.stem for f in PRESETS_DIR.glob("*.json")]
        raise FileNotFoundError(
            f"Preset '{name}' not found. Available: {', '.join(sorted(available))}"
        )

    with open(filepath) as f:
        data = json.load(f)

    if "tickers" not in data:
        raise ValueError(f"Preset '{name}' missing required 'tickers' field")

    return Preset(
        name=data.get("name", name),
        description=data.get("description", ""),
        tickers=data["tickers"],
        pairs=data.get("pairs", []),
        sector=data.get("sector", ""),
        sub_industry=data.get("sub_industry", ""),
        vol_driver=data.get("vol_driver", ""),
        source=data.get("source", ""),
        groups=data.get("groups", {}),
    )


def get_preset_tickers(name: str) -> List[str]:
    """Convenience: load preset and return just tickers."""
    return load_preset(name).tickers


def get_preset_pairs(name: str) -> List[List[str]]:
    """Convenience: load preset and return just pairs."""
    return load_preset(name).pairs


if __name__ == "__main__":
    print("Available presets:\n")
    for name, desc, count in list_presets():
        print(f"  {name:30s}  {count:>4d} tickers  {desc}")
