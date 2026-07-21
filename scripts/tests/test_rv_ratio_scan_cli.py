"""RV-ratio script CLI contract (rv-ratio plan step 5).

Exactly one JSON document on stdout, progress on stderr
(feedback_subprocess_progress_to_stderr), atomic_save disk write,
non-zero exit + no snapshot on benchmark failure. History layer is
mocked; window-relative dates only.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import rv_ratio_scan as rv


def _dates(n: int) -> list[str]:
    return [(date.today() - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _gbm_series(n: int, seed: int, vol: float = 0.012) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(0.0003, vol, n - 1)
    closes = np.concatenate(([100.0], 100.0 * np.exp(np.cumsum(log_returns))))
    return {d: float(c) for d, c in zip(_dates(n), closes)}


ASSET_SERIES = _gbm_series(300, seed=7)
BENCH_SERIES = _gbm_series(300, seed=8, vol=0.008)


@pytest.fixture
def writer_mod():
    """Resolve db.writer at test time: earlier suite files may have replaced
    the sys.modules entry, and _persist_snapshot imports it lazily."""
    return importlib.import_module("db.writer")


@pytest.fixture
def snapshot_recorder(monkeypatch: pytest.MonkeyPatch, writer_mod) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        writer_mod,
        "upsert_rv_ratio_snapshot",
        lambda symbol, taken_at, payload: calls.append((symbol, taken_at, payload)),
    )
    return calls


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(rv, "_PROJECT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def happy_history(monkeypatch: pytest.MonkeyPatch):
    def _ensure(symbol: str):
        if symbol == rv.BENCHMARK:
            return dict(BENCH_SERIES), ["yahoo"]
        return dict(ASSET_SERIES), ["yahoo", "ib"]

    monkeypatch.setattr(rv, "ensure_history", _ensure)


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["rv_ratio_scan.py", *argv])
    rv.main()


class TestStdoutDiscipline:
    def test_stdout_is_exactly_one_json_document(
        self, monkeypatch, capsys, snapshot_recorder, project_dir, happy_history
    ):
        _run_main(monkeypatch, ["SMH"])

        out, err = capsys.readouterr()
        payload = json.loads(out)  # raises if anything but one JSON doc
        assert payload["symbol"] == "SMH"
        assert payload["missing"] is False
        assert payload["sources"] == {"asset": ["yahoo", "ib"], "benchmark": ["yahoo"]}
        assert err.strip()  # progress went to stderr

    def test_symbol_is_uppercased(
        self, monkeypatch, capsys, snapshot_recorder, project_dir, happy_history
    ):
        _run_main(monkeypatch, ["smh"])
        assert json.loads(capsys.readouterr().out)["symbol"] == "SMH"

    def test_invalid_symbol_exits_2_with_no_stdout(self, monkeypatch, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _run_main(monkeypatch, ["BAD$YM"])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert "invalid symbol" in err.lower()


class TestPersistence:
    def test_snapshot_written_to_turso_and_disk(
        self, monkeypatch, capsys, snapshot_recorder, project_dir, happy_history
    ):
        _run_main(monkeypatch, ["SMH"])

        payload = json.loads(capsys.readouterr().out)
        assert len(snapshot_recorder) == 1
        symbol, taken_at, stored_payload = snapshot_recorder[0]
        assert symbol == "SMH"
        assert taken_at == payload["scan_time"]
        assert stored_payload == payload

        disk_file = project_dir / "data" / "rv_ratio" / "SMH.json"
        on_disk = json.loads(disk_file.read_text())
        on_disk.pop("_checksum", None)
        assert on_disk == payload

    def test_turso_write_failure_is_non_fatal(
        self, monkeypatch, capsys, project_dir, happy_history, writer_mod
    ):
        def _boom(*args, **kwargs):
            raise RuntimeError("turso down")

        monkeypatch.setattr(writer_mod, "upsert_rv_ratio_snapshot", _boom)

        _run_main(monkeypatch, ["SMH"])

        out, err = capsys.readouterr()
        assert json.loads(out)["missing"] is False
        assert "non-fatal" in err
        assert (project_dir / "data" / "rv_ratio" / "SMH.json").exists()

    def test_missing_variant_writes_no_snapshot_or_disk(
        self, monkeypatch, capsys, snapshot_recorder, project_dir
    ):
        short = _gbm_series(100, seed=9)

        def _ensure(symbol: str):
            if symbol == rv.BENCHMARK:
                return dict(BENCH_SERIES), ["yahoo"]
            return short, ["yahoo"]

        monkeypatch.setattr(rv, "ensure_history", _ensure)

        _run_main(monkeypatch, ["XYZ"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_history"
        assert snapshot_recorder == []
        assert not (project_dir / "data" / "rv_ratio").exists()


class TestBenchmarkFailure:
    def test_empty_benchmark_exits_nonzero_without_snapshot(
        self, monkeypatch, capsys, snapshot_recorder, project_dir
    ):
        def _ensure(symbol: str):
            if symbol == rv.BENCHMARK:
                return {}, []
            return dict(ASSET_SERIES), ["yahoo"]

        monkeypatch.setattr(rv, "ensure_history", _ensure)

        with pytest.raises(SystemExit) as excinfo:
            _run_main(monkeypatch, ["SMH"])

        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert out == ""  # no partial JSON on stdout
        assert rv.BENCHMARK in err
        assert snapshot_recorder == []
        assert not (project_dir / "data" / "rv_ratio").exists()
