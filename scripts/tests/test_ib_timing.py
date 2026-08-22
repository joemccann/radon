"""PhaseTimer: connect / qualify / sleep / permId marks on stderr JSON."""

from __future__ import annotations

import json

from clients.ib_timing import PhaseTimer


def test_phase_timer_records_elapsed_from_injected_clock():
    ticks = iter([10.0, 10.2, 10.5, 11.0])

    timer = PhaseTimer("ib_sync", clock=lambda: next(ticks))
    timer.mark("connect")
    timer.mark("qualify")
    timer.mark("sleep")

    assert [p["phase"] for p in timer.phases] == ["connect", "qualify", "sleep"]
    assert timer.phases[0]["elapsed_s"] == 0.2
    assert timer.phases[1]["elapsed_s"] == 0.5
    assert timer.phases[2]["elapsed_s"] == 1.0


def test_phase_timer_emit_writes_stderr_json(capsys):
    ticks = iter([1.0, 1.4, 2.0])
    timer = PhaseTimer("ib_place_order", clock=lambda: next(ticks))
    timer.mark("connect")
    timer.mark("permId")
    payload = timer.emit()

    err = capsys.readouterr().err.strip()
    parsed = json.loads(err)
    assert parsed["event"] == "ib_hot_path_timing"
    assert parsed["job"] == "ib_place_order"
    assert [p["phase"] for p in parsed["phases"]] == ["connect", "permId"]
    assert parsed["total_s"] == 1.0
    assert payload == parsed
