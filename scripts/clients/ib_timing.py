"""Hot-path phase timestamps for IB portfolio sync and order placement.

These timestamps exist to prove where wall-clock goes (connect / qualify /
sleep-or-wait / permId) before anyone rewrites the client. Emit on stderr
so script stdout stays reserved for result JSON.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable, Optional


class PhaseTimer:
    """Monotonic phase marks for one IB job."""

    def __init__(
        self,
        job: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sink: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.job = job
        self._clock = clock
        self._sink = sink
        self._t0 = clock()
        self.phases: list[dict[str, Any]] = []

    def mark(self, phase: str) -> dict[str, Any]:
        elapsed_s = round(self._clock() - self._t0, 4)
        record = {"phase": phase, "elapsed_s": elapsed_s}
        self.phases.append(record)
        return record

    def as_dict(self) -> dict[str, Any]:
        total_s = self.phases[-1]["elapsed_s"] if self.phases else 0.0
        return {
            "event": "ib_hot_path_timing",
            "job": self.job,
            "phases": list(self.phases),
            "total_s": total_s,
        }

    def emit(self) -> dict[str, Any]:
        payload = self.as_dict()
        line = json.dumps(payload, separators=(",", ":"))
        if self._sink is not None:
            self._sink(line)
        else:
            print(line, file=sys.stderr)
        return payload
