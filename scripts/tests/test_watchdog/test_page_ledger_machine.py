"""watchdog_pages ledger machine: pending -> claimed -> done | pending | skipped.

* Every write after a claim is guarded by the claim token. ``claim_token=None``
  used to skip the guard, so a caller without the token could complete or
  fail a ticket another responder owned.
* ``status`` was a free string: anything could be written as a terminal state.
* ``attempts`` was read in one statement and written in another.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


def _claimed(token: str = "tok-a") -> str:
    from watchdog.pages import claim_page, enqueue_delivered_page

    pid = enqueue_delivered_page(
        service="exit-orders", severity="P1", kind="error", message="boom", now=NOW
    )
    assert claim_page(pid, now=NOW, claim_token=token)
    return pid


def _row(db_conn, pid):
    return db_conn.execute(
        "SELECT status, claim_token, attempts FROM watchdog_pages WHERE page_id=?",
        (pid,),
    ).fetchone()


class TestOwnershipIsRequired:
    def test_complete_without_token_is_refused(self, db_conn):
        from watchdog.pages import complete_page

        pid = _claimed()
        with pytest.raises(ValueError):
            complete_page(pid, status="done", result="x", now=NOW, claim_token=None)
        assert _row(db_conn, pid) == ("claimed", "tok-a", 0)

    def test_attempt_failure_without_token_is_refused(self, db_conn):
        from watchdog.pages import record_attempt_failure

        pid = _claimed()
        with pytest.raises(ValueError):
            record_attempt_failure(pid, now=NOW, error="x", claim_token=None)
        assert _row(db_conn, pid) == ("claimed", "tok-a", 0)

    def test_foreign_token_attempt_failure_changes_nothing(self, db_conn):
        from watchdog.pages import record_attempt_failure

        pid = _claimed()
        status = record_attempt_failure(pid, now=NOW, error="x", claim_token="tok-b")
        assert status == "claimed"
        assert _row(db_conn, pid) == ("claimed", "tok-a", 0)


class TestStatusIsAnEnum:
    def test_unknown_status_is_refused(self, db_conn):
        from watchdog.pages import complete_page

        pid = _claimed()
        with pytest.raises(ValueError):
            complete_page(pid, status="finished", result="x", now=NOW, claim_token="tok-a")
        assert _row(db_conn, pid) == ("claimed", "tok-a", 0)

    def test_non_terminal_status_is_refused(self, db_conn):
        from watchdog.pages import complete_page

        pid = _claimed()
        with pytest.raises(ValueError):
            complete_page(pid, status="pending", result="x", now=NOW, claim_token="tok-a")

    def test_terminal_status_lands(self, db_conn):
        from watchdog.pages import PageStatus, complete_page

        pid = _claimed()
        complete_page(pid, status=PageStatus.DONE, result="x", now=NOW, claim_token="tok-a")
        assert _row(db_conn, pid) == ("done", None, 0)


class TestAttemptsAreAtomic:
    def test_concurrent_increment_is_not_lost(self, db_conn, monkeypatch):
        """Another writer bumps ``attempts`` right after this call's first
        statement. A separate SELECT then UPDATE overwrote that bump."""
        import db.hrana_http as hrana_mod
        from watchdog.pages import record_attempt_failure

        pid = _claimed()
        raced = {"done": False}
        real_query, real_execute = hrana_mod.hrana_query, hrana_mod.hrana_execute

        def race():
            if not raced["done"]:
                raced["done"] = True
                db_conn.execute(
                    "UPDATE watchdog_pages SET attempts = attempts + 1 WHERE page_id=?",
                    (pid,),
                )

        def query(sql, args=(), timeout=None):
            rows = real_query(sql, args, timeout)
            race()
            return rows

        def execute(sql, args=(), timeout=None):
            real_execute(sql, args, timeout)
            race()

        monkeypatch.setattr(hrana_mod, "hrana_query", query)
        monkeypatch.setattr(hrana_mod, "hrana_execute", execute)
        assert record_attempt_failure(pid, now=NOW, error="x", claim_token="tok-a") == "pending"
        assert db_conn.execute(
            "SELECT attempts FROM watchdog_pages WHERE page_id=?", (pid,)
        ).fetchone() == (2,)

    def test_third_failure_skips(self, db_conn):
        from watchdog.pages import claim_page, record_attempt_failure

        pid = _claimed("t1")
        assert record_attempt_failure(pid, now=NOW, error="x", claim_token="t1") == "pending"
        assert claim_page(pid, now=NOW, claim_token="t2")
        assert record_attempt_failure(pid, now=NOW, error="x", claim_token="t2") == "pending"
        assert claim_page(pid, now=NOW, claim_token="t3")
        assert record_attempt_failure(pid, now=NOW, error="x", claim_token="t3") == "skipped"
        assert _row(db_conn, pid) == ("skipped", None, 3)
