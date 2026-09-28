"""fx InvalidChunk / notice-429 / loop-guard classifiers / security deliver.

2026-09-27: the Mac mini runner's six nightly wrappers shared copy-pasted fx
handling that dropped InvalidChunk, threw away ``[notice] ⚠ Rate limited ·
HTTP 429`` lines, and treated fx's tool-loop guard as a bare FAILED or
rc=0-INCOMPLETE. These fixtures are the real log lines. The loops that ran fx
rungs moved to scripts/runner/run_loop.sh; the classifiers stay in the two
security wrappers and are pinned here.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_fxerr", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)

REPO = _h.REPO
BASH = _h.BASH
LOOPS = _h.LOOPS
LOOP_IDS = sorted(LOOPS)

FX_INVALID_CHUNK = "fx: InvalidChunk"
FX_NOTICE_429 = (
    '[notice] ⚠ Rate limited · HTTP 429: {"status":429,"title":"Too Many Requests"}'
    " · retrying request"
)
FX_NOTICE_429_WAIT = (
    '[notice] ⚠ Rate limited · HTTP 429: {"status":429,"title":"Too Many Requests"}'
    " · retrying request in 16s"
)
FX_NOTICE_INVALID_CHUNK = (
    "[notice] ⚠ Rate limited · InvalidChunk · server requested a longer wait"
    " · recovery paused · attempt 350"
)
FX_RECOVERED = "✓ recovered · succeeded on attempt 12"
FX_LOOP_IDENTICAL = (
    "Repeated identical shell failures stopped the tool loop. The failed "
    "action was not retried again; inspect the environment or change the "
    "action before continuing."
)
FX_LOOP_VALIDATION = "Repeated shell validation failures stopped the tool loop."


def _extract_fn(src: str, name: str) -> str:
    needle = f"{name}() {{"
    start = src.index(needle)
    depth = 0
    for i, ch in enumerate(src[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unclosed {name}")


def _quota_helpers(src: str) -> str:
    return "\n".join(
        [
            _extract_fn(src, "quota_regex"),
            _extract_fn(src, "is_quota_exhausted"),
            _extract_fn(src, "is_transient_network_failure"),
            _extract_fn(src, "is_fx_loop_guard"),
        ]
    )


def _drive(tmp_path: Path, wrapper: Path, helpers: str, log_text: str, extra: str) -> subprocess.CompletedProcess:
    log = tmp_path / "round.log"
    log.write_text(log_text, encoding="utf-8")
    driver = tmp_path / "drive.sh"
    driver.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                helpers,
                f'RUN_LOG="{log}"',
                "ROUND_LOG_MARK=0",
                'RUNG_PROVIDER="${RUNG_PROVIDER:-fx}"',
                extra,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [BASH, str(driver)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("loop", LOOP_IDS)
class TestFxInvalidChunkIsTransient:
    def test_invalid_chunk_is_a_transient_network_failure(self, tmp_path, loop):
        src = LOOPS[loop].read_text(encoding="utf-8")
        proc = _drive(
            tmp_path, LOOPS[loop], _quota_helpers(src),
            FX_INVALID_CHUNK + "\n",
            "is_transient_network_failure\n",
        )
        assert proc.returncode == 0, (proc.stdout, proc.stderr)



@pytest.mark.parametrize("loop", LOOP_IDS)
class TestFxNotice429IsAQuotaWall:
    def test_a_failed_notice_429_tail_is_quota(self, tmp_path, loop):
        src = LOOPS[loop].read_text(encoding="utf-8")
        log = "\n".join([FX_NOTICE_429, FX_NOTICE_429_WAIT, ""])
        proc = _drive(
            tmp_path, LOOPS[loop], _quota_helpers(src),
            log,
            "is_quota_exhausted\n",
        )
        assert proc.returncode == 0, (proc.stdout, proc.stderr, log)

    def test_a_recovered_429_with_marker_is_not_a_wall(self, tmp_path, loop):
        src = LOOPS[loop].read_text(encoding="utf-8")
        log = "\n".join(
            [
                FX_NOTICE_429,
                FX_NOTICE_429_WAIT,
                FX_RECOVERED,
                "SECURITY-NIGHTLY PHASE COMPLETE: audit run_id=ok",
                "SECURITY-DEEPSEC PHASE COMPLETE: audit run_id=ok",
                "",
            ]
        )
        proc = _drive(
            tmp_path, LOOPS[loop], _quota_helpers(src),
            log,
            'RC=0\n'
            'if (( RC != 0 )) && is_quota_exhausted; then echo IS_A_WALL; exit 3; fi\n'
            'echo NOT_A_WALL\n',
        )
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert "NOT_A_WALL" in proc.stdout
        assert "IS_A_WALL" not in proc.stdout



@pytest.mark.parametrize("loop", LOOP_IDS)
class TestFxLoopGuardDropsARung:
    @pytest.mark.parametrize("line", (FX_LOOP_IDENTICAL, FX_LOOP_VALIDATION))
    def test_each_loop_guard_string_is_detected(self, tmp_path, loop, line):
        src = LOOPS[loop].read_text(encoding="utf-8")
        proc = _drive(
            tmp_path, LOOPS[loop], _quota_helpers(src),
            line + "\n",
            "is_fx_loop_guard\n",
        )
        assert proc.returncode == 0, (line, proc.stdout, proc.stderr)


def _finished_audit(loop: str, citation: str | None = None) -> str:
    """Column-0 phase marker, optionally after an indented fixture citation."""
    lines = []
    if citation:
        lines.append("    " + citation)
    if loop == "security":
        lines.append("SECURITY-NIGHTLY PHASE COMPLETE: audit run_id=ok")
    elif loop == "security-deepsec":
        lines.append("SECURITY-DEEPSEC PHASE COMPLETE: audit run_id=ok")
    else:
        raise AssertionError(f"no phase marker for {loop}")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("loop", LOOP_IDS)
class TestFxLoopGuardIgnoresAQuotedFixture:
    """R-709 / REL-290: a citation of the fixture is not the CLI's verdict.

    Security and DeepSec refuse a non-Claude ladder, so only the classifier
    is run here; provider parity keeps that function byte-identical.
    """

    @pytest.mark.parametrize("line", (FX_LOOP_IDENTICAL, FX_LOOP_VALIDATION))
    def test_an_indented_citation_is_not_the_verdict(self, tmp_path, loop, line):
        src = LOOPS[loop].read_text(encoding="utf-8")
        proc = _drive(
            tmp_path, LOOPS[loop], _quota_helpers(src),
            _finished_audit(loop, line),
            "is_fx_loop_guard\n",
        )
        assert proc.returncode != 0, (loop, line, proc.stdout, proc.stderr)

    @pytest.mark.parametrize("line", (FX_LOOP_IDENTICAL, FX_LOOP_VALIDATION))
    def test_the_cli_final_line_is_the_verdict(self, tmp_path, loop, line):
        src = LOOPS[loop].read_text(encoding="utf-8")
        proc = _drive(
            tmp_path, LOOPS[loop], _quota_helpers(src),
            line + "\n",
            "is_fx_loop_guard\n",
        )
        assert proc.returncode == 0, (loop, line, proc.stdout, proc.stderr)

    def test_a_column0_sentence_above_the_marker_is_not_the_final_line(
        self, tmp_path, loop
    ):
        src = LOOPS[loop].read_text(encoding="utf-8")
        log = FX_LOOP_IDENTICAL + "\n" + _finished_audit(loop)
        proc = _drive(
            tmp_path, LOOPS[loop], _quota_helpers(src),
            log,
            "is_fx_loop_guard\n",
        )
        assert proc.returncode != 0, (loop, proc.stdout, proc.stderr)


def _security_deliver_driver(tmp_path: Path, loop: str, log_text: str, record: dict | None):
    wrapper = LOOPS[loop]
    src = wrapper.read_text(encoding="utf-8")
    constants = []
    for name in (
        "PHASE_COMPLETE_MARKER",
        "DELIVER_READY_MARKER",
        "DELIVER_INCOMPLETE_MARKER",
    ):
        match = re.search(rf'^{name}="[^"]+"', src, re.M)
        assert match, name
        constants.append(match.group(0))
    helpers = "\n".join(
        [
            *constants,
            _extract_fn(src, "deliver_record_fresh_terminal"),
            _extract_fn(src, "phase_marker_in_slice"),
            _extract_fn(src, "phase_marker_present"),
            _extract_fn(src, "deliver_phase_resume_detail"),
        ]
    )
    root = tmp_path / "weekend"
    rec_dir = root / f".{loop.replace('security-deepsec', 'security-deepsec')}-deliver"
    # LOOP_SLUG matches the wrapper: security / security-deepsec
    slug = "security-deepsec" if loop == "security-deepsec" else "security"
    rec_dir = root / f".{slug}-deliver"
    rec_dir.mkdir(parents=True)
    if record is not None:
        (rec_dir / "record.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    log = tmp_path / "round.log"
    log.write_bytes(log_text.encode("utf-8"))
    driver = tmp_path / "deliver_drive.sh"
    driver.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                helpers,
                f'RUN_LOG="{log}"',
                "ROUND_LOG_MARK=0",
                'PHASE="deliver"',
                f'LOOP_SLUG="{slug}"',
                f'RADON_WEEKEND_ROOT="{root}"',
                f"PHASE_START_EPOCH={int(datetime.now(timezone.utc).timestamp()) - 30}",
                "RC=0",
                'status="OK"',
                'if [[ "$status" == "OK" ]] && ! phase_marker_present; then',
                '  status="INCOMPLETE (exit 0 without the phase-completion marker)"',
                "  RC=75",
                "fi",
                'if [[ "$PHASE" == "deliver" && "$status" == "OK" ]]; then',
                '  status="0 PR(s), nothing to merge"',
                "fi",
                'detail="$(deliver_phase_resume_detail "$status")"',
                'printf "STATUS=%s\\nRC=%s\\nDETAIL=%s\\n" "$status" "$RC" "$detail"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [BASH, str(driver)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


SEP27_MARKER = "SECURITY-NIGHTLY PHASE COMPLETE: deliver run_id=20260927-deliver\n"
SEP27_MARKER_DEEPSEC = "SECURITY-DEEPSEC PHASE COMPLETE: deliver run_id=20260927-deliver\n"


@pytest.mark.parametrize(
    "loop,marker",
    (
        ("security", SEP27_MARKER),
        ("security-deepsec", SEP27_MARKER_DEEPSEC),
    ),
)
class TestSecurityDeliverAcceptsAFreshGreenRecord:
    def test_marker_plus_fresh_green_prs0_record_is_complete(self, tmp_path, loop, marker):
        rec = {
            "loop": "security" if loop == "security" else "security-deepsec",
            "branch": "",
            "pr": None,
            "url": None,
            "status": "green",
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        proc = _security_deliver_driver(tmp_path, loop, marker, rec)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert "STATUS=0 PR(s), nothing to merge" in proc.stdout, proc.stdout
        assert "RC=0" in proc.stdout, proc.stdout

    def test_marker_without_a_record_is_incomplete_without_sha_wording(
        self, tmp_path, loop, marker
    ):
        proc = _security_deliver_driver(tmp_path, loop, marker, None)
        assert "RC=75" in proc.stdout, (proc.stdout, proc.stderr)
        assert "INCOMPLETE" in proc.stdout, proc.stdout
        assert "audited SHA was NOT advanced" not in proc.stdout, proc.stdout

    def test_marker_plus_a_stale_record_is_incomplete_without_sha_wording(
        self, tmp_path, loop, marker
    ):
        rec = {
            "loop": "security" if loop == "security" else "security-deepsec",
            "branch": "",
            "pr": None,
            "url": None,
            "status": "green",
            "updated_at": "2020-01-01T00:00:00+00:00",
        }
        proc = _security_deliver_driver(tmp_path, loop, marker, rec)
        assert "RC=75" in proc.stdout, (proc.stdout, proc.stderr)
        assert "INCOMPLETE" in proc.stdout, proc.stdout
        assert "audited SHA was NOT advanced" not in proc.stdout, proc.stdout
