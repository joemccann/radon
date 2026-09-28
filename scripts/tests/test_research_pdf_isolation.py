"""Intake v2 opens an untrusted research PDF only inside the bounded research.pdf subprocess."""
import json
import subprocess
import sys

import pytest

from research import intake, pipeline
from research.figures import Catalogue
from research.pipeline import EvidenceError


class _Done:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture
def runs(monkeypatch):
    calls = []

    def record(result):
        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if isinstance(result, BaseException):
                raise result
            return result
        monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    record.calls = calls
    return record


def test_intake_defaults_parse_the_pdf_out_of_process(tmp_path):
    pipe = intake.Pipeline(tmp_path, reviewer=None, publisher=None)
    assert pipe.figure_catalogue is pipeline.figures_isolated
    assert pipe.pdf_created is pipeline.creation_date_isolated


def test_figures_isolated_runs_the_bounded_child_without_worker_credentials(runs, monkeypatch, tmp_path):
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "worker-secret")
    stdout = json.dumps({"figures": [{"id": "f1", "page": 2}], "skipped_pages": [{"page": 3, "reason": "too many objects"}]})
    runs(_Done(stdout=stdout.encode()))
    extras = [{"page": 2, "bbox": [0.1, 0.1, 0.5, 0.5]}]

    result = pipeline.figures_isolated(tmp_path / "doc.pdf", [1, 2, 3], tmp_path / "figures", extras=extras)

    (command, kwargs), = runs.calls
    assert command == [sys.executable, "-m", "research.pdf", str(tmp_path / "doc.pdf"), str(tmp_path / "figures"),
                       "--figures-only", "--pages", "1,2,3"]
    assert json.loads(kwargs["input"]) == extras
    assert kwargs["timeout"] == 180 and kwargs["capture_output"] is True
    assert "TURSO_AUTH_TOKEN" not in kwargs["env"]
    assert isinstance(result, Catalogue)
    assert list(result) == [{"id": "f1", "page": 2}]
    assert result.skipped_pages == [{"page": 3, "reason": "too many objects"}]


def test_figures_isolated_child_failure_is_an_evidence_error(runs, tmp_path):
    runs(_Done(returncode=-9))
    with pytest.raises(EvidenceError):
        pipeline.figures_isolated(tmp_path / "doc.pdf", [1], tmp_path / "figures")


def test_creation_date_isolated_reads_metadata_in_the_child(runs, monkeypatch, tmp_path):
    monkeypatch.setenv("UW_TOKEN", "worker-secret")
    runs(_Done(stdout=json.dumps({"created": "D:20260908124300+01'00'"}).encode()))

    assert pipeline.creation_date_isolated(tmp_path / "doc.pdf") == "D:20260908124300+01'00'"
    (command, kwargs), = runs.calls
    assert command == [sys.executable, "-m", "research.pdf", str(tmp_path / "doc.pdf"), ".", "--metadata-only"]
    assert "UW_TOKEN" not in kwargs["env"]


@pytest.mark.parametrize("outcome", [_Done(returncode=1), subprocess.TimeoutExpired("research.pdf", 30), _Done(stdout=b"not json")])
def test_creation_date_isolated_is_none_when_the_child_cannot_answer(runs, tmp_path, outcome):
    runs(outcome)
    assert pipeline.creation_date_isolated(tmp_path / "doc.pdf") is None
