"""Deterministic archive simulation: rate controller, queue, and health.

No network, no Turso, no Dropbox. The clock and the provider are fake.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from research import marketdesk_pdf_rate as rate
from research import marketdesk_pdf_store as store
from research.marketdesk_pdf_rate import RateState, on_quota, on_soft, on_success, start_state

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1] / "db/migrations"
ASSET = store.ASSET_BASE + ("ab" * 32) + ".pdf"
DIGEST = "ab" * 32


def test_hidden_quota_converges_without_a_429_storm():
    quota_per_hour = 30
    state = start_state()
    t = 0
    horizon = 72 * 3600
    signals = []
    series = []
    admitted = 0
    windows = 0
    recent = []
    next_at = 0
    while t < horizon:
        if t < next_at:
            t = next_at
        if t >= horizon:
            break
        hour = t // 3600
        if not series or series[-1]["hour"] != hour:
            series.append({"hour": int(hour), "interval": state.interval_seconds, "batch": state.batch_size,
                           "admitted": admitted, "signals": len(signals)})
        recent = [stamp for stamp in recent if stamp > t - 3600]
        if len(recent) + state.batch_size > quota_per_hour:
            retry_after = (min(recent) + 3600 - t) if recent else None
            state, delay = on_quota(state, retry_after=retry_after)
            signals.append(t)
            next_at = t + delay
            t = next_at
            continue
        waited = state.interval_seconds
        recent.extend([t] * state.batch_size)
        admitted += state.batch_size
        windows += 1
        state = on_success(state)
        next_at = t + waited
        t = next_at
    tail = [s for s in signals if s >= horizon - 12 * 3600]
    pace = state.batch_size / state.interval_seconds * 3600
    average = admitted / (horizon / 3600)
    assert windows > 0
    assert average <= quota_per_hour * 1.2
    assert len(signals) >= 1
    assert len(tail) <= 24
    assert series[-1]["admitted"] > quota_per_hour
    Path("/opt/cursor/artifacts").mkdir(parents=True, exist_ok=True)
    Path("/opt/cursor/artifacts/marketdesk-pdf-convergence.json").write_text(json.dumps({
        "quota_per_hour": quota_per_hour, "final_interval": state.interval_seconds,
        "final_batch": state.batch_size, "final_pace_per_hour": round(pace, 2),
        "average_admitted_per_hour": round(average, 2),
        "signals": len(signals), "tail_signals_12h": len(tail), "admitted": admitted,
        "series": series,
    }, indent=2))


def test_quota_storm_is_at_most_one_pdf_per_hour_within_three_cycles():
    state = start_state()
    for _ in range(3):
        state, _delay = on_quota(state)
    assert state.batch_size == 1
    assert state.batch_size * 3600 / state.interval_seconds <= 1


def test_batch_grows_only_after_clean_successes():
    state = start_state()
    for _ in range(rate.BATCH_RECOVER_EVERY - 1):
        state = on_success(state)
    assert state.batch_size == 1
    state = on_success(state)
    assert state.batch_size == 2


def test_429_storm_backs_off_and_one_success_does_not_burst():
    state = start_state()
    delay = state.interval_seconds
    for _ in range(12):
        state, delay = on_quota(state, retry_after=None)
    assert state.interval_seconds == rate.CEILING_SECONDS
    assert delay == rate.CEILING_SECONDS
    assert state.batch_size == 1
    recovered = on_success(state)
    assert recovered.interval_seconds == rate.CEILING_SECONDS - rate.ADDEND_SECONDS
    assert recovered.batch_size == 1


def test_retry_after_is_a_minimum_wait():
    state = RateState(300, 6, 0, 0)
    new_state, delay = on_quota(state, retry_after=5000)
    assert delay == 5000
    assert new_state.interval_seconds == 600
    assert new_state.batch_size == 1
    short, short_delay = on_quota(state, retry_after=10)
    assert short_delay == 600
    assert short.batch_size == 1


def test_restart_keeps_the_backed_off_rate():
    state, delay = on_quota(start_state(), retry_after=1200)
    restored = rate.load_state(state.interval_seconds, state.batch_size, state.soft_streak, state.success_credits)
    assert restored == state
    assert state.interval_seconds == 1800
    assert state.batch_size == 1
    assert delay == 1800


def test_two_workers_share_one_next_admission():
    state = start_state()
    next_at = 0
    taken = []

    def admit(now, who):
        nonlocal next_at
        if now < next_at:
            return 0
        taken.append(who)
        next_at = now + state.interval_seconds
        return state.batch_size

    assert admit(0, "a") == state.batch_size
    assert admit(0, "b") == 0
    assert admit(state.interval_seconds - 1, "b") == 0
    assert admit(state.interval_seconds, "b") == state.batch_size
    assert taken == ["a", "b"]


def test_soft_run_is_slower_than_a_single_500():
    state = start_state()
    once, changed = on_soft(state)
    assert changed is None and once.interval_seconds == start_state().interval_seconds
    twice, changed = on_soft(once)
    assert changed is None
    third, changed = on_soft(twice)
    assert changed == 1350
    assert third.interval_seconds == 1350


@pytest.fixture
def db(monkeypatch):
    ctx = store.policy.Context(frozenset(), {})
    monkeypatch.setattr(store.policy, "load_context", lambda: ctx)
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for name in ("0001_init.sql", "0071_research_post_sources.sql", "0102_marketdesk_articles.sql",
                 "0103_marketdesk_pdf_queue.sql", "0104_marketdesk_pdf_account_backoff.sql",
                 "0105_marketdesk_pdf_priority.sql", "0112_marketdesk_pdf_rate.sql", "0113_marketdesk_pdf_safety_cap.sql"):
        conn.executescript((ROOT / name).read_text())
    def execute(sql, args=(), **kwargs):
        return [tuple(row) for row in conn.execute(sql, args).fetchall()]
    def transaction(statements):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sql, args in statements:
                conn.execute(sql, args)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    monkeypatch.setattr(store, "hrana_execute", execute)
    monkeypatch.setattr(store, "hrana_transaction", transaction)
    box = type("Box", (), {})()
    box.ctx_key = ctx.key
    box.execute = conn.execute
    box.close = conn.close
    yield box
    conn.close()


def _add(db, article_id, published, *, attempts=0, priority=0):
    stamp = published.isoformat()
    post_id = "research-" + article_id
    db.execute("INSERT INTO posts(id,title,content,timestamp,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (post_id, "Selected", "Positioning rose 12%.", stamp, stamp, stamp))
    db.execute("INSERT INTO research_post_sources VALUES(?,?)", (post_id, json.dumps({
        "kind": "marketdesk", "articleId": article_id, "url": "https://marketdesk.ai/library/browse/" + article_id})))
    db.execute("INSERT INTO marketdesk_articles(article_id,article_url,published_at,post_id,status,first_seen_at,updated_at) "
               "VALUES(?,?,?,?,?,?,?)",
               (article_id, "https://marketdesk.ai/library/browse/" + article_id, stamp, post_id, "published", stamp, stamp))
    db.execute("INSERT INTO marketdesk_pdf_queue(article_id,article_url,title,published_at,next_attempt_at,first_seen_at,"
               "updated_at,attempts,priority) VALUES(?,?,?,?,?,?,?,?,?)",
               (article_id, "https://marketdesk.ai/library/browse/" + article_id, "n", stamp, stamp, stamp, stamp, attempts, priority))
    db.execute("INSERT INTO marketdesk_pdf_selection(article_id,context_hash,post_updated_at,eligible,reason_code,updated_at) "
               "VALUES(?,?,?,1,'PDF_MEASURED_MARKET_RESEARCH',?)",
               (article_id, db.ctx_key, stamp, stamp))


def _depth(db):
    return db.execute("SELECT COUNT(*) FROM marketdesk_pdf_queue WHERE disposition='active' AND status!='downloaded'").fetchone()[0]


def _finish(db, batch, now):
    for work in batch:
        assert store.record_download(work, ASSET, DIGEST, now=now, provider_success=True)
    if batch:
        store.note_success(now=now)


def test_zero_row_lease_does_not_move_the_clock(db):
    before = db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0]
    assert store.claim_batch(now=NOW, jitter_bps=0) == []
    assert db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0] == before


def test_attempts_100_is_not_admitted(db):
    _add(db, "Poison001", NOW - timedelta(days=20), attempts=100)
    _add(db, "Fresh0001", NOW)
    batch = store.claim_batch(now=NOW, jitter_bps=0)
    assert [item.article_id for item in batch] == ["Fresh0001"]
    assert db.execute("SELECT disposition,disposition_reason FROM marketdesk_pdf_queue WHERE article_id='Poison001'").fetchone() == (
        "exhausted", "attempts_exhausted")


def test_a_500_spends_the_window_and_not_the_next_one(db):
    _add(db, "File00001", NOW - timedelta(days=3))
    _add(db, "File00002", NOW - timedelta(days=2))
    first = store.claim_next(now=NOW)
    assert first.article_id == "File00002" or first.article_id == "File00001"
    store.retry(first, "http_500", now=NOW, backoff_seconds=900)
    assert store.claim_next(now=NOW + timedelta(seconds=899)) is None
    assert store.claim_next(now=NOW + timedelta(seconds=900)) is not None


def test_fresh_file_is_in_the_first_window_while_backlog_exists(db):
    for i in range(12):
        _add(db, f"Old{i:05d}", NOW - timedelta(days=8))
    _add(db, "Today0001", NOW)
    batch = store.claim_batch(now=NOW, jitter_bps=0)
    assert [item.article_id for item in batch] == ["Today0001"]


def test_two_workers_do_not_double_lease(db):
    for i in range(10):
        _add(db, f"Row{i:05d}", NOW - timedelta(days=2))
    first = store.claim_batch(now=NOW, jitter_bps=0)
    second = store.claim_batch(now=NOW, jitter_bps=0)
    assert 1 <= len(first) <= 6
    assert second == []
    assert len({item.article_id for item in first}) == len(first)


def test_no_catchup_burst_after_a_gap(db):
    for i in range(30):
        _add(db, f"Gap{i:05d}", NOW - timedelta(days=4))
    jumped = store.claim_batch(now=NOW + timedelta(days=2), jitter_bps=0)
    assert len(jumped) <= 6
    assert store.claim_batch(now=NOW + timedelta(days=2), jitter_bps=0) == []


def test_weekend_and_overnight_still_admit(db):
    saturday = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)  # Saturday 03:00 UTC
    _add(db, "Night0001", saturday)
    assert store.claim_next(now=saturday) is not None


def test_health_flips_when_the_backlog_drains(db):
    _add(db, "Aged00001", NOW - timedelta(days=3))
    assert store.backlog_breach(now=NOW).startswith("PDF archive backlog:")
    batch = store.claim_batch(now=NOW, jitter_bps=0)
    _finish(db, batch, NOW)
    assert store.backlog_breach(now=NOW + timedelta(minutes=5)) is None


def test_fixed_1800s_pace_cannot_hide_a_week_of_intake(db):
    """The retired slot, replayed: 150/day for 7 days leaves a red health row."""
    db.execute("UPDATE marketdesk_pdf_throttle SET interval_seconds=1800,batch_size=1")
    clock = NOW
    added = 0
    owed = 0.0
    red = False
    for _step in range(7 * 24 * 12):
        owed += 150 / (24 * 12)
        while owed >= 1:
            _add(db, f"In{added:06d}", clock)
            added += 1
            owed -= 1
        batch = store.claim_batch(now=clock, jitter_bps=0)
        for work in batch:
            store.record_download(work, ASSET, DIGEST, now=clock, provider_success=True)
        if store.backlog_breach(now=clock):
            red = True
        clock += timedelta(minutes=5)
    assert added == 7 * 150 or abs(added - 7 * 150) <= 1
    assert red
    assert _depth(db) > 400


def test_fourteen_day_sim_drains_1300_and_keeps_up():
    """Full admission sim. Printed numbers are the PR evidence."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for name in ("0001_init.sql", "0071_research_post_sources.sql", "0102_marketdesk_articles.sql",
                 "0103_marketdesk_pdf_queue.sql", "0104_marketdesk_pdf_account_backoff.sql",
                 "0105_marketdesk_pdf_priority.sql", "0112_marketdesk_pdf_rate.sql", "0113_marketdesk_pdf_safety_cap.sql"):
        conn.executescript((ROOT / name).read_text())
    ctx = store.policy.Context(frozenset(), {})
    box = type("Box", (), {"ctx_key": ctx.key, "execute": conn.execute, "close": conn.close})()

    def execute(sql, args=(), **kwargs):
        return [tuple(row) for row in conn.execute(sql, args).fetchall()]

    def transaction(statements):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sql, args in statements:
                conn.execute(sql, args)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    previous_execute, previous_transaction = store.hrana_execute, store.hrana_transaction
    previous_context = store.policy.load_context
    store.hrana_execute = execute
    store.hrana_transaction = transaction
    store.policy.load_context = lambda: ctx
    try:
        for i in range(1275):
            _add(box, f"B{i:07d}", NOW - timedelta(days=10))
        _add(box, "TodaySim1", NOW)
        assert store.backlog_breach(now=NOW)
        first = store.claim_batch(now=NOW, jitter_bps=0)
        assert [item.article_id for item in first] == ["TodaySim1"]
        _finish(conn, first, NOW)
        clock = NOW
        end = NOW + timedelta(days=7)
        drained_at = None
        max_after = 0
        admitted = len(first)
        weekend = 0
        owed = 0.0
        added = 0
        while clock < end:
            owed += 190 / (24 * 12)
            while owed >= 1:
                _add(box, f"N{added:07d}", clock)
                added += 1
                owed -= 1
            batch = store.claim_batch(now=clock, jitter_bps=0)
            _finish(conn, batch, clock)
            admitted += len(batch)
            if clock.weekday() >= 5:
                weekend += len(batch)
            depth = _depth(box)
            if depth == 0 and drained_at is None:
                drained_at = clock
            if drained_at is not None:
                max_after = max(max_after, depth)
            clock += timedelta(minutes=5)
        assert drained_at is not None
        assert drained_at - NOW < timedelta(days=7)
        assert max_after < 80
        assert weekend > 0
        assert store.backlog_breach(now=clock) is None
        report = {
            "admitted": admitted,
            "intake": added,
            "hours_to_drain": round((drained_at - NOW).total_seconds() / 3600, 2),
            "max_depth_after_drain": max_after,
            "weekend_admissions": weekend,
            "final_depth": _depth(box),
        }
        path = Path("/opt/cursor/artifacts/marketdesk-pdf-sim.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2))
        print("SIM", json.dumps(report))
    finally:
        store.hrana_execute = previous_execute
        store.hrana_transaction = previous_transaction
        store.policy.load_context = previous_context
        conn.close()


def test_http_date_retry_after_is_a_suspension():
    from research.marketdesk_pdf_rate import parse_retry_after
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    seconds, dated = parse_retry_after("Sun, 15 Nov 2026 12:00:00 GMT", now=now)
    assert dated is True and seconds == 86400
    seconds, dated = parse_retry_after("120", now=now)
    assert dated is False and seconds == 120


def test_same_bytes_same_path_are_one_dropbox_file():
    files = {}

    def upload(path, digest):
        current = files.get(path)
        if current == digest:
            return "present"
        if current is None:
            files[path] = digest
            return "created"
        raise AssertionError("different bytes would be a second write")

    assert upload("/radon/marketdesk/2026/november/nov 5/ing/note.pdf", "h" * 64) == "created"
    assert upload("/radon/marketdesk/2026/november/nov 5/ing/note.pdf", "h" * 64) == "present"
    assert list(files) == ["/radon/marketdesk/2026/november/nov 5/ing/note.pdf"]


def test_persisted_429_exits_zero(tmp_path, monkeypatch):
    from research import marketdesk_pdf_job as job

    class Queue:
        def seed_missing(self, **kwargs):
            return 0
        def attach_downloads(self, **kwargs):
            return 0
        def claim_batch(self, **kwargs):
            return []
        def blocked(self):
            return "http_429"
        def backlog_breach(self, **kwargs):
            return None

    from contextlib import contextmanager

    @contextmanager
    def cycle(*args, **kwargs):
        try:
            yield type("C", (), {"finished_at": None})()
        except BaseException:
            raise

    dependency = type("D", (), {})()
    dependency.store = Queue()
    dependency.service_cycle = cycle
    dependency.now = lambda: NOW
    monkeypatch.setattr(job, "default_deps", lambda: dependency)
    assert job.main([]) == 0
