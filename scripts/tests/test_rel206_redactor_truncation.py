"""REL-206 (R-565): the redactor survives truncation — a secret cut mid-value
by a byte-bounded tail must not pass verbatim."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import weekend_redact  # noqa: E402

# Built at runtime so gitleaks' generic rule never sees an assignment shape.
SECRET = "tok-" + "abcdefghij0123456789"


class TestTruncatedFragments:
    def test_a_tail_boundary_suffix_fragment_is_scrubbed(self):
        fragment = SECRET[6:]  # the head was cut off by tail -c
        out = weekend_redact.redact(f"...{fragment} end of log", [SECRET])
        assert fragment not in out

    def test_a_prefix_fragment_is_scrubbed(self):
        fragment = SECRET[:-6]  # the tail was cut off at the byte bound
        out = weekend_redact.redact(f"log starts {fragment}", [SECRET])
        assert fragment not in out

    def test_no_fragment_longer_than_the_floor_survives(self):
        floor = weekend_redact.MIN_VALUE_LEN
        for cut in range(1, len(SECRET) - floor):
            out = weekend_redact.redact(f"x {SECRET[cut:]} y", [SECRET])
            surviving = [
                SECRET[i:j]
                for i in range(len(SECRET))
                for j in range(i + floor, len(SECRET) + 1)
                if SECRET[i:j] in out
            ]
            assert not surviving, f"cut={cut} left {surviving[:2]}"

    def test_short_common_fragments_are_left_alone(self):
        # "tok-" is under the floor; scrubbing it would shred ordinary text.
        out = weekend_redact.redact("a tok- prefix in prose", [SECRET])
        assert "tok-" in out
