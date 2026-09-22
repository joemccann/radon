"""Fortune's Formula implement tests — spec §D rows D1-D5, D7-D9, D12 CLI."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from scripts.kelly import (
    KELLY_MAX_FRACTION,
    kelly,
    kelly_config,
    kelly_size_batch,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_KELLY_SCRIPT = Path(__file__).resolve().parent.parent / "kelly.py"
_SCRIPTS = _REPO_ROOT / "scripts"
_LIB = _REPO_ROOT / "lib"


def _run_cli(args, env=None, check=True):
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)
    return subprocess.run(
        [sys.executable, str(_KELLY_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=cmd_env,
    )


class TestD1FullKellyBan:
    def test_d1_fraction_one_raises(self):
        with pytest.raises(ValueError, match="banned|0\\.5"):
            kelly(0.6, 2.0, fraction=1.0)

    def test_d1_fraction_above_max_raises(self):
        with pytest.raises(ValueError):
            kelly(0.6, 2.0, fraction=0.51)

    def test_d1_half_kelly_measured_succeeds(self):
        result = kelly(0.6, 2.0, fraction=0.5, p_source="measured")
        assert result["fraction_used"] == 0.5
        assert result["edge_exists"] is True

    def test_d1_cli_fraction_one_exits_2(self):
        proc = _run_cli(["--prob", "0.6", "--odds", "2", "--fraction", "1.0"])
        assert proc.returncode == 2
        assert proc.stdout == ""


class TestD2HalfKellyDefault:
    def test_d2_default_fraction_is_half(self, monkeypatch):
        monkeypatch.delenv("RADON_KELLY_FRACTION", raising=False)
        result = kelly(0.6, 2.0)
        assert result["fraction_used"] == 0.5
        assert result["fraction_source"] == "default"

    def test_d2_env_quarter_is_stricter_option(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_FRACTION", "0.25")
        result = kelly(0.6, 2.0)
        assert result["fraction_used"] == 0.25
        assert result["fraction_source"] == "env"

    def test_d2_estimated_cannot_exceed_configured_fraction(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_FRACTION", "0.25")
        with pytest.raises(ValueError, match="estimated"):
            kelly(0.6, 2.0, fraction=0.5)

    def test_d2_measured_may_use_half_when_env_is_quarter(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_FRACTION", "0.25")
        result = kelly(0.6, 2.0, fraction=0.5, p_source="measured")
        assert result["fraction_used"] == 0.5

    def test_d2_env_above_band_clamps(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_FRACTION", "0.9")
        cfg = kelly_config()
        assert cfg["fraction"] == 0.5
        result = kelly(0.6, 2.0)
        assert result["fraction_used"] == 0.5
        assert result["fraction_source"] == "env_clamped"

    def test_d2_cli_default_half_caps_at_2_5_pct(self, monkeypatch):
        monkeypatch.delenv("RADON_KELLY_FRACTION", raising=False)
        proc = _run_cli(["--prob", "0.6", "--odds", "2", "--bankroll", "100000"])
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
        assert data["fraction_used"] == 0.5
        assert data["fractional_kelly_pct"] == 20.0
        assert data["use_size"] == 2500.0
        assert data["capped"] is True


class TestD3ScalarBatchCapParity:
    def test_d3_high_edge_caps_at_2_5_pct(self, monkeypatch):
        monkeypatch.delenv("RADON_KELLY_FRACTION", raising=False)
        monkeypatch.delenv("RADON_KELLY_P_HAIRCUT", raising=False)
        scalar = kelly(0.9, 5.0, bankroll=100_000)
        assert scalar["use_size"] == 2500.0
        assert scalar["capped"] is True
        batch = kelly_size_batch([0.9], [5.0], 100_000)
        assert batch[0] == pytest.approx(scalar["use_size"])

    def test_d3_below_cap_not_capped(self, monkeypatch):
        monkeypatch.delenv("RADON_KELLY_FRACTION", raising=False)
        result = kelly(0.51, 1.02, bankroll=100_000)
        assert result["capped"] is False
        assert result["use_size"] < result["max_per_position"]

    def test_d3_property_scalar_matches_batch(self, monkeypatch):
        monkeypatch.delenv("RADON_KELLY_FRACTION", raising=False)
        monkeypatch.delenv("RADON_KELLY_P_HAIRCUT", raising=False)
        rng = np.random.default_rng(20260919)
        for _ in range(200):
            p = float(rng.uniform(0.01, 0.99))
            b = float(rng.uniform(0.1, 8.0))
            bankroll = float(rng.uniform(1_000, 500_000))
            scalar = kelly(p, b, bankroll=bankroll)
            batch = kelly_size_batch([p], [b], bankroll)
            assert scalar["use_size"] == pytest.approx(float(batch[0]), abs=1e-9)

    def test_d3_cli_use_size_never_exceeds_cap(self):
        cases = [(0.9, 5.0, 100_000), (0.51, 1.02, 100_000), (0.6, 2.0, 50_000)]
        for prob, odds, bankroll in cases:
            proc = _run_cli([
                "--prob", str(prob), "--odds", str(odds),
                "--bankroll", str(bankroll),
            ])
            assert proc.returncode == 0, proc.stderr
            data = json.loads(proc.stdout)
            assert data["use_size"] <= data["max_per_position"]


class TestD4OneCapConstant:
    def test_d4_literal_0_025_appears_once_in_kelly_py(self):
        text = (_SCRIPTS / "kelly.py").read_text()
        assert len(re.findall(r"0\.025", text)) == 1

    def test_d4_no_production_caller_passes_max_pct(self):
        hits = []
        for root in (_SCRIPTS, _LIB):
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if "tests" in path.parts or "__tests__" in path.parts:
                    continue
                if path.suffix not in {".py", ".ts", ".js"}:
                    continue
                text = path.read_text(errors="ignore")
                if path.name == "kelly.py":
                    continue
                if re.search(r"kelly_size_batch\([^)]*max_pct\s*=", text, re.S):
                    hits.append(str(path.relative_to(_REPO_ROOT)))
        assert hits == []


class TestD5NoEdgeAndBatchDomain:
    def test_d5_no_edge_zero_size(self):
        result = kelly(0.3, 1.0, bankroll=100_000)
        assert result["use_size"] == 0.0
        assert "contracts" not in result
        assert result["recommendation"] == "DO NOT BET"

    def test_d5_batch_out_of_domain_prob_is_zero(self):
        sizes = kelly_size_batch([1.5, -0.1, float("nan")], [2.0, 2.0, 2.0], 100_000)
        assert sizes[0] == pytest.approx(0.0)
        assert sizes[1] == pytest.approx(0.0)
        assert sizes[2] == pytest.approx(0.0)

    def test_d5_batch_fraction_two_raises(self):
        with pytest.raises(ValueError):
            kelly_size_batch([0.6], [2.0], 100_000, fraction=2.0)


class TestD7FatTailHaircut:
    def test_d7_haircut_moves_p_and_full_kelly(self, monkeypatch):
        monkeypatch.delenv("RADON_KELLY_P_HAIRCUT", raising=False)
        cut = kelly(0.6, 2.0, p_haircut=0.05)
        assert cut["p_effective"] == pytest.approx(0.55)
        assert cut["full_kelly_pct"] == pytest.approx(32.5)
        raw = kelly(0.6, 2.0, p_haircut=0.0)
        assert raw["full_kelly_pct"] == pytest.approx(40.0)

    def test_d7_haircut_above_band_raises(self):
        with pytest.raises(ValueError):
            kelly(0.6, 2.0, p_haircut=0.3)

    def test_d7_env_haircut_applies_when_arg_none(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_P_HAIRCUT", "0.05")
        result = kelly(0.6, 2.0)
        assert result["p_effective"] == pytest.approx(0.55)
        assert result["p_haircut"] == pytest.approx(0.05)


class TestD8GeometricFraming:
    def test_d8_growth_positive_under_edge(self):
        result = kelly(0.6, 2.0, fraction=0.5, p_source="measured")
        assert result["edge_exists"] is True
        assert result["growth_rate_used"] > 0
        assert result["growth_rate_full"] >= result["growth_rate_used"]

    def test_d8_p_one_growth_full_is_none(self):
        result = kelly(1.0, 3.0)
        assert result["growth_rate_full"] is None

    def test_d8_half_kelly_is_about_75_pct_of_full_growth(self):
        result = kelly(0.6, 2.0, fraction=0.5, p_source="measured")
        assert result["growth_rate_used"] == pytest.approx(
            0.75 * result["growth_rate_full"], rel=0.05
        )


class TestD9RestructureFlag:
    def test_d9_high_full_kelly_restructures(self):
        assert kelly(0.7, 4.0)["restructure"] is True

    def test_d9_quarter_full_restructures(self):
        assert kelly(0.55, 1.5)["restructure"] is True

    def test_d9_boundary_is_strict(self):
        result = kelly(0.52, 1.5)
        assert result["full_kelly_pct"] == pytest.approx(20.0)
        assert result["restructure"] is False
