"""REL-021b / R-030: NTP steps must not change elapsed-time cadence."""
from datetime import datetime, timedelta

import pytest

from monitor_daemon.handlers import base


class Clock(datetime):
    wall = datetime(2026, 9, 29, 10)

    @classmethod
    def now(cls, tz=None):
        return cls.wall.replace(tzinfo=tz)


class Handler(base.BaseHandler):
    interval_seconds = 60
    def execute(self):
        return {}


@pytest.fixture
def clocks(monkeypatch):
    from types import SimpleNamespace
    Clock.wall = datetime(2026, 9, 29, 10)
    tick = [100.0]
    monkeypatch.setattr(base, "datetime", Clock)
    monkeypatch.setattr(base, "time", SimpleNamespace(monotonic=lambda: tick[0]), raising=False)
    return tick


@pytest.mark.parametrize("wall_step,elapsed,due", [(-3600, 60, True), (3600, 30, False)])
def test_clock_step_neither_stalls_nor_accelerates_handler(clocks, wall_step, elapsed, due):
    handler = Handler()
    assert handler.run()["status"] == "ok"
    Clock.wall += timedelta(seconds=wall_step)
    clocks[0] += elapsed
    assert handler.is_due() is due


def test_elapsed_duration_cannot_turn_negative_after_clock_rollback(clocks):
    class Rollback(Handler):
        def execute(self):
            Clock.wall -= timedelta(hours=1)
            clocks[0] += 2
            return {}
    assert Rollback().run()["elapsed_ms"] == 2000


def test_restored_future_wall_timestamp_waits_at_most_one_interval(clocks):
    handler = Handler()
    handler.set_state({"last_run": (Clock.wall + timedelta(hours=1)).isoformat()})
    assert handler.is_due() is False
    clocks[0] += 60
    assert handler.is_due() is True
