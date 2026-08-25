"""REL-052 tranche F — R-126, R-131, R-136, R-137, R-138.

Five places the 2026-08-22 delta reports a failure (or a fabrication) as a
benign, healthy outcome: a 30-session window called a 52-week extreme, a
dead token monitor heartbeating `ok`, a cancel path that can raise over the
CancelledError, a permission repair that is a no-op on the only route that
is used, and a FRED overlay that swallows a revoked key.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# R-126 — a short window is not a 52-week range
# --------------------------------------------------------------------------
def _series(n: int, start: float = 1.0, step: float = 0.01) -> list[dict]:
    return [
        {
            "date": f"2026-0{1 + i // 28}-{1 + i % 28:02d}",
            "iei_close": 100.0,
            "hyg_close": 80.0,
            "ratio": start + step * i,
            "dxy_close": None,
        }
        for i in range(n)
    ]


class TestIeiHygNeedsAFullWindow:
    def test_a_short_window_is_not_a_new_extreme(self):
        import fetch_iei_hyg as mod

        current = mod._current(_series(30))
        assert current["state"] == "unknown", (
            "30 sessions after first deploy the latest row IS the window "
            "extreme almost every day, so the panel printed NEW 52W HIGH "
            "off a month of data"
        )
        assert current["ratio_pct_rank"] is None

    def test_a_short_window_says_how_short_it_is(self):
        import fetch_iei_hyg as mod

        current = mod._current(_series(30))
        assert current["window_sessions"] == 30
        assert current["window_complete"] is False

    def test_a_full_window_still_classifies(self):
        import fetch_iei_hyg as mod

        current = mod._current(_series(mod.MIN_OBSERVATIONS))
        assert current["window_complete"] is True
        assert current["state"] == "new_high"
        assert current["ratio_pct_rank"] == pytest.approx(1.0)

    def test_the_minimum_matches_the_window_it_claims(self):
        import fetch_iei_hyg as mod

        assert mod.MIN_OBSERVATIONS == mod.WINDOW_SESSIONS

    def test_the_web_type_knows_the_unknown_state(self):
        src = (REPO / "web" / "lib" / "ieiHyg.ts").read_text()
        assert '"unknown"' in src.split("export type IeiHygState")[1].split("\n")[0]
        assert "window_complete" in src


# --------------------------------------------------------------------------
# R-131 — a monitor that cannot monitor is not healthy
# --------------------------------------------------------------------------
class TestFlexTokenCheckFailsLoud:
    def test_a_missing_config_is_a_soft_failure_not_a_skip(self, tmp_path, monkeypatch):
        from monitor_daemon.handlers import flex_token_check as mod
        from monitor_daemon.handlers.base import HandlerSoftFailure

        monkeypatch.setattr(mod, "CONFIG_PATH", tmp_path / "absent.json")
        handler = mod.FlexTokenCheck.__new__(mod.FlexTokenCheck)
        # `skip` was a SUCCESS to BaseHandler, so a deleted config heartbeated
        # a healthy flex-token-check daily while the token marched to expiry.
        with pytest.raises(HandlerSoftFailure, match="flex_token_config"):
            handler._execute_inner()

    def test_a_config_without_an_expiry_is_a_soft_failure(self, tmp_path, monkeypatch):
        from monitor_daemon.handlers import flex_token_check as mod
        from monitor_daemon.handlers.base import HandlerSoftFailure

        cfg = tmp_path / "flex_token_config.json"
        cfg.write_text('{"breadcrumb": "x"}')
        monkeypatch.setattr(mod, "CONFIG_PATH", cfg)
        handler = mod.FlexTokenCheck.__new__(mod.FlexTokenCheck)
        with pytest.raises(HandlerSoftFailure, match="expires_at"):
            handler._execute_inner()

    def test_a_valid_config_is_still_a_normal_result(self, tmp_path, monkeypatch):
        from monitor_daemon.handlers import flex_token_check as mod

        cfg = tmp_path / "flex_token_config.json"
        expires = (datetime.now(timezone.utc) + timedelta(days=200)).isoformat()
        cfg.write_text(f'{{"expires_at": "{expires}"}}')
        monkeypatch.setattr(mod, "CONFIG_PATH", cfg)
        handler = mod.FlexTokenCheck.__new__(mod.FlexTokenCheck)
        # The happy path returns a domain-specific payload with no `status`
        # key at all, which BaseHandler already treats as success.
        assert handler._execute_inner().get("status") != "error"

    def test_the_soft_failure_reaches_base_handlers_no_latch_path(self):
        from monitor_daemon.handlers.base import BaseHandler, HandlerSoftFailure

        with pytest.raises(HandlerSoftFailure):
            BaseHandler._enforce_return_contract(
                {"status": "error", "reason": "flex_token_config.json not found"}
            )


# --------------------------------------------------------------------------
# R-136 — cancellation must survive a child that already exited
# --------------------------------------------------------------------------
class _Proc:
    def __init__(self, *, kill_raises=False, wait_hangs=False):
        self.returncode = None
        self.kill_raises = kill_raises
        self.wait_hangs = wait_hangs
        self.killed = False

    def kill(self):
        self.killed = True
        if self.kill_raises:
            raise ProcessLookupError(3, "No such process")

    async def wait(self):
        if self.wait_hangs:
            await asyncio.sleep(3600)
        return 0


class TestCancelPathIsBounded:
    def test_a_vanished_child_does_not_replace_the_cancellederror(self):
        from api.subprocess import _terminate_child

        proc = _Proc(kill_raises=True)
        asyncio.run(_terminate_child(proc))  # must not raise
        assert proc.killed

    def test_a_wedged_child_does_not_hold_the_cancelled_task_forever(self):
        from api import subprocess as mod

        proc = _Proc(wait_hangs=True)

        async def go():
            started = asyncio.get_running_loop().time()
            await mod._terminate_child(proc, reap_timeout=0.05)
            return asyncio.get_running_loop().time() - started

        elapsed = asyncio.run(go())
        assert elapsed < 1.0, "await proc.wait() is unbounded on the cancel path"

    def test_both_cancel_branches_go_through_the_helper(self):
        src = (REPO / "scripts" / "api" / "subprocess.py").read_text()
        for marker in ("except asyncio.CancelledError:",):
            for block in src.split(marker)[1:]:
                head = block.split("raise")[0]
                assert "_terminate_child" in head, (
                    "a cancel branch still calls proc.kill() directly"
                )

    def test_children_are_signalled_as_a_group(self):
        src = (REPO / "scripts" / "api" / "subprocess.py").read_text()
        assert "start_new_session=True" in src, (
            "only the direct child was signalled, so a script that spawns "
            "its own children left them holding IB client ids"
        )


# --------------------------------------------------------------------------
# R-137 — a permission repair that never runs is not a repair
# --------------------------------------------------------------------------
class TestMediaPermissionsOnTheRemoteRoute:
    def test_rsync_sets_the_destination_mode_itself(self):
        src = (REPO / "scripts" / "newsfeed" / "push_media.js").read_text()
        assert "--chmod=F644" in src, (
            "localMediaDest returns null for radon@ib-gateway:/…, so the "
            "post-transfer chmod never runs on the documented laptop->Hetzner "
            "path; rsync's own --chmod is the only thing that reaches it"
        )

    def test_pre_existing_files_can_still_have_their_mode_repaired(self):
        src = (REPO / "scripts" / "newsfeed" / "push_media.js").read_text()
        args = src.split("const args = [")[1].split("];")[0]
        assert '"--ignore-existing"' not in args, (
            "--ignore-existing skips every pre-fix 0600 image forever, so "
            "they stay 403 on media.radon.run permanently"
        )

    def test_the_remote_route_is_not_silent(self):
        src = (REPO / "scripts" / "newsfeed" / "push_media.js").read_text()
        assert "repairMode" in src

    def test_the_chmod_sweep_only_touches_media_extensions(self):
        src = (REPO / "scripts" / "newsfeed" / "mediaPermissions.js").read_text()
        assert "PUBLIC_MEDIA_EXTENSIONS" in src, (
            "an operator-supplied dir was chmod 0644 for EVERY regular file, "
            "so a stray cookie jar under a Caddy web root became world-readable"
        )


# --------------------------------------------------------------------------
# R-138 — a dead FRED key is not "not released yet"
# --------------------------------------------------------------------------
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _econ_row(**over):
    row = {
        "type": "economic",
        "title": "Initial Jobless Claims",
        "actual": "",
        "prev": "230K",
        "event_time": "2026-08-21T12:30:00Z",
        "date": "2026-08-21",
    }
    row.update(over)
    return row


class TestFredOverlayIsHonest:
    def test_a_failing_series_is_logged(self, capsys):
        import fetch_catalysts as mod

        def boom(_series_id):
            raise RuntimeError("FRED_API_KEY not set")

        mod.apply_fred_actuals([_econ_row()], boom, NOW)
        assert "FRED" in capsys.readouterr().err

    def test_a_failing_series_is_attempted_once_per_run(self):
        import fetch_catalysts as mod

        calls = []

        def boom(series_id):
            calls.append(series_id)
            raise RuntimeError("429")

        mod.apply_fred_actuals([_econ_row(), _econ_row()], boom, NOW)
        assert len(calls) == 1

    def test_a_print_equal_to_the_prior_one_is_still_a_print(self):
        import fetch_catalysts as mod

        rows = [_econ_row(prev="230K")]
        mod.apply_fred_actuals(
            rows, lambda _s: {"date": "2026-08-21", "value": "230000"}, NOW
        )
        assert rows[0]["actual"] not in ("", None), (
            "UNRATE repeats month-over-month and ICSA ties weekly; equal to "
            "prev is a real, released figure, not 'no print'"
        )

    def test_a_genuinely_absent_observation_still_leaves_actual_blank(self):
        import fetch_catalysts as mod

        rows = [_econ_row()]
        mod.apply_fred_actuals(rows, lambda _s: {"date": "2026-08-21", "value": "."}, NOW)
        assert rows[0]["actual"] == ""


# --------------------------------------------------------------------------
# TEST_AUDIT T-127 — every spawner detaches the child into its own session,
# and the cancel helper never SIGKILLs the caller's own process group.
# The substring test above is satisfied by two of three sites; run_module
# (used by /blotter's Flex fetch, routinely >120s under a throttle) spawned
# in uvicorn's group, so its timeout path killpg'd radon-api itself.
# --------------------------------------------------------------------------
_SLEEPING_CHILD = [sys.executable, "-c", "import time; time.sleep(30)"]


def _spawn_via(spawner_name: str):
    """Drive a real spawner with a real (sleeping) child; record the child's
    process group while it is alive, before the spawner's timeout kills it."""
    import asyncio as _asyncio

    from api import subprocess as mod

    observed: dict = {}
    real_exec = _asyncio.create_subprocess_exec

    async def recording_exec(*cmd, **kwargs):
        proc = await real_exec(*_SLEEPING_CHILD, **kwargs)
        observed["child_pgid"] = os.getpgid(proc.pid)
        observed["kwargs"] = kwargs
        return proc

    async def go():
        # The spawner's timeout path killpg's the child's group. Before the
        # fix that group IS this test process's, so record instead of
        # delivering — the red must not SIGKILL the runner to show itself.
        with patch.object(mod.asyncio, "create_subprocess_exec", recording_exec), patch(
            "api.subprocess.os.killpg",
            side_effect=lambda pgid, sig: observed.setdefault("killpg", []).append(pgid),
        ):
            spawner = getattr(mod, spawner_name)
            if spawner_name == "run_module":
                await spawner("time", [], timeout=0.2)
            else:
                await spawner(str(REPO / "scripts" / "fetch_ticker.py"), [], timeout=0.2)
        return observed

    return _asyncio.run(go())


class TestChildrenRunInTheirOwnProcessGroup:
    @pytest.mark.parametrize("spawner", ["run_script", "run_script_raw", "run_module"])
    def test_child_is_not_in_the_callers_group(self, spawner):
        observed = _spawn_via(spawner)
        assert "child_pgid" in observed, "spawner never reached create_subprocess_exec"
        assert observed["child_pgid"] != os.getpgid(os.getpid()), (
            f"{spawner} spawned in the caller's process group; its timeout "
            "path would killpg the API server itself"
        )
        assert os.getpgid(os.getpid()) not in observed.get("killpg", [])

    def test_terminate_child_refuses_to_kill_its_own_group(self):
        import asyncio as _asyncio
        import subprocess as _sp

        from api.subprocess import _terminate_child

        # Same group as this test process (no new session), exactly what
        # run_module produced before the fix.
        child = _sp.Popen(_SLEEPING_CHILD)
        killpg_calls: list = []
        try:
            with patch("api.subprocess.os.killpg", side_effect=lambda pgid, sig: killpg_calls.append(pgid)):

                class _Adapter:
                    """asyncio-Process shape over a Popen child."""
                    pid = child.pid

                    @property
                    def returncode(self):
                        return child.poll()

                    def kill(self):
                        child.kill()

                    async def wait(self):
                        return child.wait()

                _asyncio.run(_terminate_child(_Adapter(), reap_timeout=5.0))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        assert os.getpgid(os.getpid()) not in killpg_calls, (
            "_terminate_child SIGKILLed the caller's own process group"
        )
        assert child.poll() is not None, "child was not killed directly"
