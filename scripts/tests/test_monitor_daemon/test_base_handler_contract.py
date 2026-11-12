#!/usr/bin/env python3
"""Contract tests for ``BaseHandler.run()`` return-value semantics.

Production bug (2026-05-14): a single 60s IBKR Flex timeout cost ~7 days
of cash flow data because the handler RETURNED ``{"status": "error", ...}``
instead of raising. ``BaseHandler.run()`` saw a returned dict, latched
``last_run``, and ``is_due()`` for the daily-windowed handler refused
to re-fire for 24h. Multiplied across the week of silent failures, the
panel went stale.

A three-layer fix was shipped specifically for ``cash_flow_sync``. These
tests make the bug structurally impossible for ALL daily / market-hours
handlers by enforcing the contract at ``BaseHandler.run()``:

    {"status": "error", ...}  → soft failure; ``HandlerSoftFailure`` raised
                                BEFORE ``last_run`` is touched, so the next
                                daemon cycle re-evaluates ``is_due``.
    any other dict (or {})    → success / legit skip / domain-specific
                                signal (``ok``, ``skip``, ``healthy``,
                                ``healed``, ``throttled``, etc.); latches
                                ``last_run`` normally. Only ``error``
                                carries no-latch semantics.
    raise <any exc>           → hard failure; last_run NOT latched
                                (existing behavior preserved).

Plus a contract-lint test: scan every handler under
``scripts/monitor_daemon/handlers/`` and FAIL the suite if any
``return {"status": "error"`` pattern is present in handler code.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.base import BaseHandler, HandlerSoftFailure


# --------------------------------------------------------------------- helpers


def _make_handler(return_value):
    """Return a one-shot handler whose execute() returns ``return_value``."""

    class _OneShot(BaseHandler):
        name = "contract_oneshot"
        interval_seconds = 60
        requires_market_hours = False

        def execute(self) -> dict:  # type: ignore[override]
            return return_value

    return _OneShot()


def _make_raising_handler(exc: BaseException):
    class _Raiser(BaseHandler):
        name = "contract_raiser"
        interval_seconds = 60
        requires_market_hours = False

        def execute(self) -> dict:  # type: ignore[override]
            raise exc

    return _Raiser()


# ---------------------------------------------------------------------- ok path


class TestStatusOkLatches:
    def test_ok_status_latches_last_run(self):
        handler = _make_handler({"status": "ok", "summary": "done"})
        assert handler.last_run is None

        result = handler.run()

        assert handler.last_run is not None
        assert result["status"] == "ok"
        assert result["data"]["status"] == "ok"

    def test_ok_status_blocks_immediate_rerun(self):
        handler = _make_handler({"status": "ok"})
        handler.run()
        # last_run set to ~now; interval 60s → not due immediately
        assert handler.is_due() is False

    def test_payload_without_status_is_still_ok(self):
        """Legacy handlers that return arbitrary dicts without a 'status'
        key are treated as success — the contract only constrains dicts
        that DO declare a status.
        """
        handler = _make_handler({"orders_checked": 3, "fills": []})
        result = handler.run()
        assert handler.last_run is not None
        assert result["status"] == "ok"


# --------------------------------------------------------------------- skip path


class TestStatusSkipLatches:
    def test_skip_status_latches_last_run(self):
        handler = _make_handler({"status": "skip", "reason": "not configured"})
        assert handler.last_run is None

        result = handler.run()

        assert handler.last_run is not None
        assert result["status"] == "ok"
        assert result["data"]["status"] == "skip"


# -------------------------------------------------------------------- error path


class TestStatusErrorDoesNotLatch:
    def test_error_status_raises_handler_soft_failure(self):
        """The smoking gun: a returned ``status=error`` dict must surface
        as a ``HandlerSoftFailure`` so the daemon's circuit breaker /
        short-embargo state can record the failure and so ``last_run``
        is never touched.
        """
        handler = _make_handler({"status": "error", "error": "flex timeout"})
        assert handler.last_run is None

        result = handler.run()  # BaseHandler catches and wraps

        # last_run MUST NOT be set — that was the 2026-05-14 bug.
        assert handler.last_run is None
        # Wrapper surface still reports error to the daemon log path.
        assert result["status"] == "error"
        assert "flex timeout" in result["error"]

    def test_error_status_preserves_is_due_for_next_cycle(self):
        handler = _make_handler({"status": "error", "error": "boom"})
        handler.run()

        # No last_run latch → is_due stays True so the next 30s loop
        # re-evaluates immediately (within-day retry).
        assert handler.is_due() is True

    def test_error_payload_passed_through_on_soft_failure(self):
        captured: list[HandlerSoftFailure] = []

        class _Capturing(BaseHandler):
            name = "capturing"
            interval_seconds = 60
            requires_market_hours = False

            def execute(self) -> dict:  # type: ignore[override]
                return {"status": "error", "error": "rate limit", "code": 1001}

        handler = _Capturing()

        # Patch run() to also capture the raised exception while keeping
        # the normal wrapping behavior.
        original = handler.run

        def _spy():
            try:
                return original()
            except HandlerSoftFailure as exc:  # pragma: no cover
                captured.append(exc)
                raise

        handler.run = _spy  # type: ignore[assignment]
        result = handler.run()
        # Even though run() wraps the exception, we can introspect via
        # the wrapped error string (the cheapest pin without coupling
        # to logger output).
        assert result["status"] == "error"
        assert "rate limit" in result["error"]


# ----------------------------------------------------------- domain-specific status


class TestDomainSpecificStatusLatches:
    """Handlers may use richer status vocabularies
    (``healthy``, ``healed``, ``throttled``, ``heal_failed``) for their
    own bookkeeping. The contract only intercepts ``error`` — every
    other status is treated as success and latches ``last_run`` so the
    interval gate works as expected.
    """

    def test_healthy_status_latches(self):
        handler = _make_handler({"status": "healthy", "wal_conflicts_5m": 0})
        result = handler.run()
        assert handler.last_run is not None
        assert result["status"] == "ok"

    def test_throttled_status_latches(self):
        handler = _make_handler({"status": "throttled", "since_last_heal_s": 120})
        result = handler.run()
        assert handler.last_run is not None
        assert result["status"] == "ok"

    def test_heal_failed_status_latches(self):
        """``heal_failed`` is a RETRY signal — the watchdog itself
        chooses not to advance its throttle so the next 60s cycle
        retries. It is NOT the soft-failure-error path; ``last_run``
        latches and the handler's own throttle owns retry spacing.
        """
        handler = _make_handler({"status": "heal_failed", "error": "systemctl failed"})
        result = handler.run()
        assert handler.last_run is not None
        assert result["status"] == "ok"


# --------------------------------------------------------------------- exceptions


class TestUncaughtExceptionDoesNotLatch:
    def test_runtime_error_does_not_latch(self):
        handler = _make_raising_handler(RuntimeError("kaboom"))
        result = handler.run()

        assert handler.last_run is None
        assert result["status"] == "error"
        assert "kaboom" in result["error"]


# ------------------------------------------------------------- contract lint scan


HANDLERS_DIR = SCRIPTS_DIR / "monitor_daemon" / "handlers"


def _iter_handler_files() -> list[Path]:
    """Every Python file under handlers/ except the contract base
    itself and ``__init__``. ``base.py`` is excluded because its
    ``run()`` wrapper legitimately constructs a ``{"status": "error"}``
    envelope when catching an exception — that's the daemon's outer
    surface, not a handler's ``execute()`` return.

    Helpers (``_throttle_backoff``) are scanned too — they don't run
    as handlers today, but a future migration into a handler must
    trip the lint.
    """
    skip = {"__init__.py", "base.py"}
    return sorted(p for p in HANDLERS_DIR.glob("*.py") if p.name not in skip)


def _has_status_error_return(source: str) -> list[tuple[int, str]]:
    """AST-walk: collect every ``return`` whose value is a Dict literal
    with a ``"status"`` key whose value is the string ``"error"``.
    """
    tree = ast.parse(source)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            continue
        for key, val in zip(value.keys, value.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "status"
                and isinstance(val, ast.Constant)
                and val.value == "error"
            ):
                offenders.append((node.lineno, ast.unparse(value)))
    return offenders


class TestContractLint:
    """Every handler module under ``scripts/monitor_daemon/handlers/``
    must raise on soft failures — never ``return {"status": "error", ...}``.

    This test is the structural guardrail behind the
    ``BaseHandler.run()`` contract: even if a future handler forgets to
    wire ``HandlerSoftFailure`` explicitly, the lint trips first.
    """

    def test_no_handler_returns_status_error(self):
        offenders: dict[str, list[tuple[int, str]]] = {}
        for path in _iter_handler_files():
            source = path.read_text(encoding="utf-8")
            hits = _has_status_error_return(source)
            if hits:
                offenders[str(path.relative_to(SCRIPTS_DIR.parent))] = hits

        if offenders:
            lines = ["Found return {'status': 'error', ...} in handler code:"]
            for rel, hits in offenders.items():
                for lineno, src in hits:
                    lines.append(f"  {rel}:{lineno}: {src}")
            lines.append(
                "Soft failures must raise (HandlerSoftFailure or a "
                "specific exception) so BaseHandler.run() does not latch "
                "last_run."
            )
            pytest.fail("\n".join(lines))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
