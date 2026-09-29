"""REL-052 / NF-5: quota accounting cannot pin a worker or tear its history."""
import fcntl
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from utils import uw_budget

NOW = datetime(2026, 9, 29, 15, tzinfo=timezone.utc)


def test_held_lock_refuses_within_bound_and_recovers_after_release(tmp_path):
    target = tmp_path / "budget.json"
    state = {"date": uw_budget.quota_date(NOW), "count": 7}
    target.write_text(json.dumps(state))
    code = """
import sys
from datetime import datetime
from utils import uw_budget
uw_budget.LOCK_TIMEOUT_S = 0.05
try:
    uw_budget.record_hit(path=sys.argv[1], now=datetime.fromisoformat(sys.argv[2]))
except TimeoutError:
    raise SystemExit(75)
"""
    lock = target.with_name(target.name + ".lock")
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        result = subprocess.run([sys.executable, "-c", code, str(target), NOW.isoformat()],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=3)
        assert result.returncode == 75, result.stderr
        assert json.loads(target.read_text()) == state
    result = subprocess.run([sys.executable, "-c", code, str(target), NOW.isoformat()],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_text())["count"] == 8


def test_failed_history_write_preserves_the_last_complete_archive(tmp_path, monkeypatch):
    target = tmp_path / "budget.json"
    history = uw_budget._history_path(target)
    previous = '{"date":"2026-09-27","count":123}\n'
    history.write_text(previous)
    write = Path.write_text

    def interrupted(path, text, *args, **kwargs):
        if path == history or path == history.with_suffix(history.suffix + ".tmp"):
            write(path, text[:8], *args, **kwargs)
            raise OSError("injected disk full")
        return write(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", interrupted)
    uw_budget._archive_unlocked(target, {"date": "2026-09-28", "count": 200})
    assert history.read_text() == previous


def test_unreadable_history_is_not_replaced_by_a_new_empty_history(tmp_path, monkeypatch):
    target = tmp_path / "budget.json"
    history = uw_budget._history_path(target)
    previous = '{"date":"2026-09-27","count":123}\n'
    history.write_text(previous)
    read = Path.read_text

    def unavailable(path, *args, **kwargs):
        if path == history:
            raise PermissionError("injected read failure")
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unavailable)
    uw_budget._archive_unlocked(target, {"date": "2026-09-28", "count": 200})
    assert read(history) == previous
