"""Path-containment tests for scripts/monitor_daemon/handlers/preset_rebalance.py.

The S&P 500 sector / sub-industry cells are raw table cells scraped from a
world-editable Wikipedia page, and the Russell 2000 sector name comes from a
third-party JSON feed. Both are interpolated into preset filenames, so a cell
carrying path separators is an arbitrary-file-write primitive against the
radon user. These tests pin the containment.

Every path used here lives under tmp_path; no file outside a tmp dir is
created, read, or asserted on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import monitor_daemon.handlers.preset_rebalance as pr

TRAVERSAL_SECTOR = "Industrials/../../../../radon-traversal-probe"


def _nested_presets_dir(tmp_path: Path) -> Path:
    """A presets dir deep enough that a traversal escape still lands in tmp_path."""
    preset_dir = tmp_path / "root" / "data" / "presets"
    preset_dir.mkdir(parents=True)
    return preset_dir


def _escaped_files(tmp_path: Path, preset_dir: Path) -> List[Path]:
    return [
        path
        for path in tmp_path.rglob("*")
        if path.is_file() and path.parent != preset_dir
    ]


def _sp500_master(tickers: List[str]) -> Dict[str, Any]:
    return {
        "name": "sp500",
        "description": "S&P 500",
        "tickers": list(tickers),
        "pairs": [],
        "groups": {
            "technology": {
                "name": "Technology",
                "sector": "Information Technology",
                "tickers": list(tickers),
                "pairs": [],
                "vol_driver": "tech-vol",
            }
        },
    }


def _companies(prefix: str, count: int, sector: str = "Industrials") -> List[Dict[str, Any]]:
    return [
        {
            "ticker": f"{prefix}{index:04d}",
            "name": f"Company {index}",
            "sector": sector,
            "sub_industry": "Software",
            "weight": 0.1,
        }
        for index in range(count)
    ]


class TestSectorCellCannotSteerPresetPath:
    def test_sp500_sector_cell_cannot_escape_presets_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preset_dir = _nested_presets_dir(tmp_path)
        fresh = _companies("S", 3, sector="Information Technology")
        fresh[-1]["sector"] = TRAVERSAL_SECTOR
        (preset_dir / "sp500.json").write_text(
            json.dumps(_sp500_master([c["ticker"] for c in fresh]))
        )
        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)
        monkeypatch.setattr(pr, "CHANGELOG_PATH", preset_dir / "changelog.json")

        with pytest.raises(pr.InvalidConstituentData, match="unsafe preset"):
            pr.update_sp500_presets(fresh, set(), set())

        assert _escaped_files(tmp_path, preset_dir) == []

    def test_r2k_sector_name_cannot_escape_presets_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preset_dir = _nested_presets_dir(tmp_path)
        fresh = _companies("R", 4, sector="Health Care")
        fresh[-1]["sector"] = TRAVERSAL_SECTOR
        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)
        monkeypatch.setattr(pr, "CHANGELOG_PATH", preset_dir / "changelog.json")

        with pytest.raises(pr.InvalidConstituentData, match="unsafe preset"):
            pr.update_r2k_presets(fresh, set(), set())

        assert _escaped_files(tmp_path, preset_dir) == []

    def test_legitimate_sector_rollup_filenames_are_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preset_dir = _nested_presets_dir(tmp_path)
        fresh = _companies("S", 2, sector="Information Technology")
        (preset_dir / "sp500.json").write_text(
            json.dumps(_sp500_master([c["ticker"] for c in fresh]))
        )
        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)
        monkeypatch.setattr(pr, "CHANGELOG_PATH", preset_dir / "changelog.json")

        pr.update_sp500_presets(fresh, set(), set())

        assert (preset_dir / "sp500-sector-information-technology.json").exists()
        assert (preset_dir / "sp500-technology.json").exists()

    def test_legitimate_r2k_sector_filenames_are_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preset_dir = _nested_presets_dir(tmp_path)
        fresh = _companies("R", 4, sector="Oil & Gas")
        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)
        monkeypatch.setattr(pr, "CHANGELOG_PATH", preset_dir / "changelog.json")

        pr.update_r2k_presets(fresh, set(), set())

        assert (preset_dir / "r2k-oil-and-gas.json").exists()
        assert (preset_dir / "r2k-tier-top-100.json").exists()


class TestAtomicWriteContainment:
    def test_write_outside_presets_dir_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preset_dir = _nested_presets_dir(tmp_path)
        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)
        outside = preset_dir / ".." / "escaped.json"

        with pytest.raises(pr.InvalidConstituentData, match="outside"):
            pr._write_json_atomic(outside, {"source": "attacker"})

        assert _escaped_files(tmp_path, preset_dir) == []

    def test_write_inside_presets_dir_still_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preset_dir = _nested_presets_dir(tmp_path)
        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)

        pr._write_json_atomic(preset_dir / "sp500.json", {"source": "canonical"})

        assert json.loads((preset_dir / "sp500.json").read_text()) == {
            "source": "canonical"
        }


class TestConstituentTextValidation:
    def test_path_bearing_sector_is_rejected_before_any_write(self) -> None:
        fresh = _companies("S", 500, sector="Industrials")
        fresh[-1]["sector"] = TRAVERSAL_SECTOR
        current = [company["ticker"] for company in fresh]

        with pytest.raises(pr.InvalidConstituentData, match="unsafe constituent text"):
            pr.validate_constituents("sp500", fresh, current)

    def test_path_bearing_sub_industry_is_rejected(self) -> None:
        fresh = _companies("S", 500, sector="Industrials")
        fresh[-1]["sub_industry"] = "Software/../../../../etc"
        current = [company["ticker"] for company in fresh]

        with pytest.raises(pr.InvalidConstituentData, match="unsafe constituent text"):
            pr.validate_constituents("sp500", fresh, current)

    def test_control_characters_in_name_are_rejected(self) -> None:
        fresh = _companies("S", 500, sector="Industrials")
        fresh[-1]["name"] = "Acme\x00Corp"
        current = [company["ticker"] for company in fresh]

        with pytest.raises(pr.InvalidConstituentData, match="unsafe constituent text"):
            pr.validate_constituents("sp500", fresh, current)

    def test_real_gics_labels_are_accepted(self) -> None:
        fresh = _companies("S", 500, sector="Consumer Discretionary")
        fresh[0]["sub_industry"] = "Oil & Gas Exploration & Production"
        fresh[1]["sub_industry"] = "Hotels, Resorts & Cruise Lines"
        fresh[2]["sub_industry"] = "Construction Machinery & Heavy Transportation Equipment"
        fresh[3]["name"] = "1-800-FLOWERS.COM, Inc. (Class A)"
        fresh[4]["name"] = "Moody's Corporation"
        current = [company["ticker"] for company in fresh]

        pr.validate_constituents("sp500", fresh, current)


class TestBoundedUpstreamReads:
    @staticmethod
    def _mock_response(content: str) -> MagicMock:
        mock = MagicMock()
        mock.read.return_value = content.encode("utf-8")
        return mock

    def test_sp500_read_is_size_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pr, "_MAX_RESPONSE_BYTES", 1024)
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response("x" * 4096),
        ):
            with pytest.raises(pr.InvalidConstituentData, match="response is too large"):
                pr.fetch_sp500()

    def test_ndx100_read_is_size_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pr, "_MAX_RESPONSE_BYTES", 1024)
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response("x" * 4096),
        ):
            with pytest.raises(pr.InvalidConstituentData, match="response is too large"):
                pr.fetch_ndx100()

    def test_r2k_read_is_size_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pr, "_MAX_RESPONSE_BYTES", 1024)
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response("x" * 4096),
        ):
            with pytest.raises(pr.InvalidConstituentData, match="response is too large"):
                pr.fetch_r2k()

    def test_bounded_read_passes_the_cap_to_the_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pr, "_MAX_RESPONSE_BYTES", 4096)
        response = self._mock_response("<html></html>")
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen", return_value=response
        ):
            with pytest.raises(pr.InvalidConstituentData):
                pr.fetch_sp500()
        assert response.read.call_args.args[0] == 4097
