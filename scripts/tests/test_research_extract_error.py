"""PDF extract EvidenceError must keep the child's real failure reason."""
import json
import signal
import subprocess
from types import SimpleNamespace

import pytest

from research.model import classify_error
from research.pipeline import EvidenceError, Pipeline
from research.state import _persist_error

PREFIX = "PDF extraction failed; original retained for review"


def _extract(monkeypatch, *, returncode=1, stderr=b"", side_effect=None):
    def fake_run(*_args, **_kwargs):
        if side_effect is not None:
            raise side_effect
        return SimpleNamespace(returncode=returncode, stdout=b"", stderr=stderr)

    monkeypatch.setattr("research.pipeline.subprocess.run", fake_run)
    with pytest.raises(EvidenceError) as err:
        Pipeline.extract(None, "doc.pdf", "out")
    return str(err.value)


def test_extract_failure_includes_returncode_and_memoryerror(monkeypatch):
    message = _extract(
        monkeypatch,
        returncode=1,
        stderr=b"Traceback (most recent call last):\n"
               b'  File "/var/lib/radon/research/pdf.py", line 40, in parse\n'
               b"    raise MemoryError\n"
               b"MemoryError\n",
    )
    assert PREFIX in message
    assert "rc=1" in message
    assert "MemoryError" in message


def test_extract_failure_maps_negative_rc_to_signal_name(monkeypatch):
    message = _extract(monkeypatch, returncode=-int(signal.SIGXCPU), stderr=b"MemoryError\n")
    assert PREFIX in message
    assert f"rc=-{int(signal.SIGXCPU)}" in message
    assert "SIGXCPU" in message
    assert "MemoryError" in message


def test_extract_failure_redacts_secret_in_stderr(monkeypatch):
    token = "sk-ant-1234567890abcdef"
    message = _extract(
        monkeypatch,
        returncode=1,
        stderr=f"Nemotron failed authorization: Bearer {token}\nValueError: parse rejected\n".encode(),
    )
    assert PREFIX in message
    assert "ValueError: parse rejected" in message
    assert token not in message
    assert "Bearer" not in message or "[redacted]" in message.lower()


@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.parametrize("line", [
    "ValueError: parse rejected authorization: Bearer sk-ant-test1234567890abcdef",
    "ValueError: parse rejected password='fake multi word password'",
    "ValueError: parse rejected https://example.invalid/report?token=fake1234567890abcdef",
    "parse rejected authorization: Bearer sk-ant-test1234567890abcdef",
])
def test_retained_error_line_is_redacted_before_health_and_journal(monkeypatch, tmp_path, timeout, line):
    """T-534: a secret on the selected line must reach neither persisted sink."""
    from research.ingestion import stage_health

    stderr = ("irrelevant diagnostic\n" + line + "\n").encode()
    expired = subprocess.TimeoutExpired("research.pdf", 180, stderr=stderr) if timeout else None
    message = _extract(monkeypatch, stderr=stderr, side_effect=expired)
    # Keep a positive control: dropping the diagnostic would also hide a secret.
    assert "parse rejected" in message
    assert "[redacted" in message
    error = EvidenceError(message)
    stage_health(tmp_path, "extraction", "error", error)
    sinks = [classify_error(error), _persist_error(error),
             json.loads((tmp_path / "health-extraction.json").read_text())["error"]]
    assert sinks == [message] * 3
    for sink in sinks:
        assert "test1234567890abcdef" not in sink
        assert "fake multi word password" not in sink
        assert "fake1234567890abcdef" not in sink
        assert len(sink) <= 480


@pytest.mark.parametrize("stderr", [None, b"", ""])
def test_empty_child_stderr_keeps_the_returncode_without_inventing_a_reason(monkeypatch, stderr):
    assert _extract(monkeypatch, stderr=stderr) == PREFIX + " (rc=1)"


def test_extract_failure_truncates_long_stderr(monkeypatch):
    blob = ("x" * 800) + "\nMemoryError: " + ("n" * 800)
    message = _extract(monkeypatch, returncode=1, stderr=blob.encode())
    assert PREFIX in message
    assert "rc=1" in message
    assert "MemoryError" in message
    assert len(message) <= 480
    assert "n" * 400 not in message


def test_extract_failure_keeps_existing_prefix(monkeypatch):
    message = _extract(monkeypatch, returncode=1, stderr=b"ValueError: boom\n")
    assert message.startswith(PREFIX)


def test_extract_timeout_keeps_prefix_and_reason(monkeypatch):
    expired = subprocess.TimeoutExpired("research.pdf", 180, output=b"", stderr=b"ValueError: hung parse\n")
    message = _extract(monkeypatch, side_effect=expired)
    assert PREFIX in message
    assert "timeout=180" in message
    assert "ValueError: hung parse" in message


def test_extract_detail_reaches_health_and_journal(monkeypatch, tmp_path):
    from research.ingestion import stage_health

    message = _extract(monkeypatch, returncode=1, stderr=b"MemoryError\n")
    error = EvidenceError(message)
    stored = classify_error(error)
    journal = _persist_error(error)
    stage_health(tmp_path, "extraction", "error", error)
    health = json.loads((tmp_path / "health-extraction.json").read_text())
    assert PREFIX in stored and "rc=1" in stored and "MemoryError" in stored
    assert stored == journal == health["error"]
    assert len(stored) <= 480


def test_classify_error_bounds_huge_evidence_message():
    error = EvidenceError(PREFIX + " (" + ("z" * 2000) + ")")
    stored = classify_error(error)
    assert PREFIX in stored
    assert len(stored) <= 480


def test_pdf_child_prints_type_and_message_before_exit(monkeypatch, capsys):
    from research import pdf as module

    monkeypatch.setattr(module, "main", lambda: (_ for _ in ()).throw(MemoryError("2GiB rlimit")))
    with pytest.raises(SystemExit) as err:
        module.run_as_child()
    assert err.value.code == 1
    assert "MemoryError: 2GiB rlimit" in capsys.readouterr().err
