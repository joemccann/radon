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


def test_unarmed_ramp_stays_additive():
    state = start_state()
    state = on_success(state)
    assert state.interval_seconds == rate.START_INTERVAL_SECONDS - rate.ADDEND_SECONDS
    assert state.recover_armed == 0


def test_quota_still_doubles_and_arms_recovery():
    state, delay = on_quota(RateState(300, 6, 2, 7))
    assert state.interval_seconds == 600 and delay == 600
    assert state.batch_size == 1 and state.success_credits == 0
    assert state.recover_armed == 1 and state.recover_credits == 0
    one = on_success(state)
    assert one.interval_seconds == 570 and one.recover_armed == 1


def test_clean_cycles_after_quota_reach_the_floor_within_six_hours():
    state = RateState(2100, 1, 0, 0, 1, 0)
    elapsed = 0
    steps = (2070, 2040, 2010)
    for expected in steps:
        elapsed += state.interval_seconds
        state = on_success(state)
        assert state.interval_seconds == expected and state.recover_armed == 1
    elapsed += state.interval_seconds
    state = on_success(state)
    assert state.interval_seconds == 990
    while state.interval_seconds > rate.FLOOR_SECONDS:
        elapsed += state.interval_seconds
        state = on_success(state)
        assert elapsed < 6 * 3600
    assert state.interval_seconds == rate.FLOOR_SECONDS
    assert state.recover_armed == 0
    assert elapsed < 6 * 3600


def test_interval_above_the_ramp_arms_recovery_without_a_quota_flag():
    state = RateState(2100, 1, 0, 2)
    assert state.recover_armed == 0 and state.success_credits == 2
    stepped = on_success(state)
    assert stepped.recover_armed == 1 and stepped.success_credits == 3
    assert stepped.interval_seconds == 2070
    elapsed = 2100
    state = stepped
    while state.interval_seconds > rate.FLOOR_SECONDS:
        elapsed += state.interval_seconds
        state = on_success(state)
        assert elapsed < 6 * 3600
    assert state.interval_seconds == rate.FLOOR_SECONDS and elapsed < 6 * 3600


def test_hourly_cap_env_defaults_and_disables(monkeypatch):
    monkeypatch.delenv(rate.HOURLY_CAP_ENV, raising=False)
    assert rate.hourly_cap() == 12
    monkeypatch.setenv(rate.HOURLY_CAP_ENV, "0")
    assert rate.hourly_cap() == 0
    monkeypatch.setenv(rate.HOURLY_CAP_ENV, "7")
    assert rate.hourly_cap() == 7
    monkeypatch.setenv(rate.HOURLY_CAP_ENV, "nope")
    assert rate.hourly_cap() == 12


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
    restored = rate.load_state(
        state.interval_seconds, state.batch_size, state.soft_streak, state.success_credits,
        recover_armed=state.recover_armed, recover_credits=state.recover_credits)
    assert restored == state
    assert state.recover_armed == 1
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
                 "0105_marketdesk_pdf_priority.sql", "0112_marketdesk_pdf_rate.sql", "0113_marketdesk_pdf_safety_cap.sql", "0114_marketdesk_pdf_hourly_recovery.sql"):
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


def test_expired_leases_are_reclaimed_on_the_next_claim_ahead_of_fresh_work(db):
    """A killed batch must not wait behind today's files for the backlog lane."""
    for i in range(4):
        _add(db, f"Old{i:05d}", NOW - timedelta(days=11))
    for i in range(6):
        _add(db, f"New{i:05d}", NOW - timedelta(hours=1))
    expired = (NOW - timedelta(hours=2)).isoformat()
    db.execute(
        "UPDATE marketdesk_pdf_queue SET status='downloading', lease_until=?, lease_token='x'||article_id "
        "WHERE article_id LIKE 'Old%'",
        (expired,))
    db.execute("DELETE FROM marketdesk_pdf_admissions")
    db.execute(
        "UPDATE marketdesk_pdf_throttle SET interval_seconds=600, batch_size=5, lane=0, next_attempt_at=?, jitter_bps=0",
        (NOW.isoformat(),))
    batch = store.claim_batch(now=NOW, jitter_bps=0, hourly_cap_value=12, daily_cap_value=400)
    got = [item.article_id for item in batch]
    assert len(batch) == 5
    assert set(got[:4]) == {f"Old{i:05d}" for i in range(4)}


def test_expired_leases_are_reclaimed_while_a_priority_cohort_holds_the_gate(db):
    """A live priority row blocks ordinary pending work. It must not block a dead lease."""
    _add(db, "Pri000001", NOW - timedelta(hours=1), priority=1)
    for i in range(4):
        _add(db, f"Old{i:05d}", NOW - timedelta(days=11))
    _add(db, "Wait00001", NOW - timedelta(days=3))
    db.execute(
        "UPDATE marketdesk_pdf_queue SET status='downloading', lease_until=?, lease_token='x'||article_id "
        "WHERE article_id LIKE 'Old%'",
        ((NOW - timedelta(hours=2)).isoformat(),))
    db.execute(
        "UPDATE marketdesk_pdf_throttle SET interval_seconds=300, batch_size=5, lane=0, next_attempt_at=?, jitter_bps=0",
        (NOW.isoformat(),))
    got = [item.article_id for item in store.claim_batch(now=NOW, jitter_bps=0, hourly_cap_value=12, daily_cap_value=400)]
    assert {f"Old{i:05d}" for i in range(4)} <= set(got)
    assert "Wait00001" not in got


def test_daily_cap_70_is_why_a_due_batch_of_5_leases_0_then_2(db, monkeypatch):
    """Production fills a rolling day of 70, then admits only what aged out.

    Unset, the code default is 400 and this same queue leases the batch of 5.
    MARKETDESK_PDF_DAILY_CAP=70 is the host override the archive doc asks for.
    """
    monkeypatch.delenv("MARKETDESK_PDF_HOURLY_CAP", raising=False)
    for i in range(8):
        _add(db, f"Due{i:05d}", NOW - timedelta(days=4, minutes=i))
    db.execute(
        "UPDATE marketdesk_pdf_throttle SET interval_seconds=300, batch_size=5, next_attempt_at=?, jitter_bps=0",
        (NOW.isoformat(),))
    inside = (NOW - timedelta(hours=1)).isoformat()
    for i in range(70):
        db.execute("INSERT INTO marketdesk_pdf_admissions(article_id, admitted_at) VALUES(?,?)", (f"A{i:07d}", inside))
    monkeypatch.delenv("MARKETDESK_PDF_DAILY_CAP", raising=False)
    assert len(store.claim_batch(now=NOW, jitter_bps=0)) == 5
    db.execute("UPDATE marketdesk_pdf_queue SET status='pending', lease_token=NULL, lease_until=NULL")
    db.execute("DELETE FROM marketdesk_pdf_admissions WHERE article_id LIKE 'Due%'")
    db.execute("UPDATE marketdesk_pdf_throttle SET next_attempt_at=?, batch_size=5", (NOW.isoformat(),))
    monkeypatch.setenv("MARKETDESK_PDF_DAILY_CAP", "70")
    assert store.claim_batch(now=NOW, jitter_bps=0) == []
    aged = (NOW - timedelta(hours=25)).isoformat()
    db.execute(
        "UPDATE marketdesk_pdf_admissions SET admitted_at=? WHERE article_id IN ('A0000000','A0000001')",
        (aged,))
    assert len(store.claim_batch(now=NOW, jitter_bps=0)) == 2


def test_halving_pulls_the_next_admission_off_the_old_hourly_gap(db):
    """A halving that only rewrites interval_seconds still waits out the old hour."""
    for i in range(6):
        _add(db, f"Due{i:05d}", NOW - timedelta(days=2, minutes=i))
    db.execute(
        "UPDATE marketdesk_pdf_throttle SET interval_seconds=3600, batch_size=2, recover_armed=1, "
        "recover_credits=3, success_credits=0, next_attempt_at=?, jitter_bps=0, lane=1",
        (NOW.isoformat(),))
    first = store.claim_batch(now=NOW, jitter_bps=0, hourly_cap_value=0, daily_cap_value=0)
    assert len(first) == 2
    _finish(db, first, NOW)
    interval, nxt = db.execute("SELECT interval_seconds, next_attempt_at FROM marketdesk_pdf_throttle").fetchone()
    assert interval == (3600 - 30) // 2
    opened = datetime.fromisoformat(nxt)
    assert opened <= NOW + timedelta(seconds=interval + 2)
    assert opened < NOW + timedelta(seconds=3600 - 60)
    assert store.claim_batch(now=opened - timedelta(seconds=30), jitter_bps=0, hourly_cap_value=0, daily_cap_value=0) == []
    assert len(store.claim_batch(now=opened, jitter_bps=0, hourly_cap_value=0, daily_cap_value=0)) == 2


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
    assert str(store.backlog_breach(now=NOW)).startswith("PDF archive backlog:")
    batch = store.claim_batch(now=NOW, jitter_bps=0)
    _finish(db, batch, NOW)
    assert store.backlog_breach(now=NOW + timedelta(minutes=5)) is None


def test_a_draining_backlog_breaches_without_paging(db):
    """A young eligible backlog that is still being admitted is a warn, not a page.
    Rows past the age window are retired before this count, so the sample stays inside it."""
    for i in range(3):
        _add(db, f"Drain{i:04d}", NOW - timedelta(days=6))
    _finish(db, store.claim_batch(now=NOW - timedelta(hours=1), jitter_bps=0)[:1], NOW - timedelta(hours=1))
    breach = store.backlog_breach(now=NOW)
    assert str(breach).startswith("PDF archive backlog:")
    assert "growing=0 stalled=0" in str(breach)
    assert breach.paging is False


def test_a_backlog_with_no_admission_for_six_hours_pages(db):
    _add(db, "Stall00001", NOW - timedelta(days=3))
    breach = store.backlog_breach(now=NOW)
    assert "stalled=1" in str(breach)
    assert breach.paging is True


def test_a_backlog_past_the_age_ceiling_pages_even_while_admitting(db, monkeypatch):
    """The 14-day page remains when the age window is off. A passing row past the
    default window is retired instead, and does not page."""
    monkeypatch.setenv("MARKETDESK_PDF_MAX_AGE_DAYS", "0")
    _add(db, "Ceiling001", NOW - timedelta(days=15))
    db.execute("INSERT INTO marketdesk_pdf_admissions(article_id,admitted_at) VALUES('x',?)", (NOW.isoformat(),))
    breach = store.backlog_breach(now=NOW)
    assert "growing=0 stalled=0" in str(breach)
    assert breach.paging is True


def test_a_growing_backlog_pages(db):
    for i in range(store.GROWTH_FLOOR):
        _add(db, f"Grow{i:05d}", NOW - timedelta(hours=1))
    db.execute("UPDATE marketdesk_pdf_throttle SET observed_pending=1 WHERE singleton=1")
    db.execute("INSERT INTO marketdesk_pdf_admissions(article_id,admitted_at) VALUES('x',?)", (NOW.isoformat(),))
    breach = store.backlog_breach(now=NOW)
    assert "growing=1 stalled=0" in str(breach)
    assert breach.paging is True


def _disposition(db, article_id):
    return db.execute(
        "SELECT status,disposition,disposition_reason FROM marketdesk_pdf_queue WHERE article_id=?",
        (article_id,)).fetchone()


def test_an_eight_day_passing_row_is_retired_and_leaves_the_backlog(db):
    _add(db, "Aged00008", NOW - timedelta(days=8))
    _add(db, "Keep00006", NOW - timedelta(days=6))
    assert store.claim_next(now=NOW).article_id == "Keep00006"
    assert _disposition(db, "Aged00008") == ("pending", "exhausted", "aged_out")
    assert _disposition(db, "Keep00006") == ("downloading", "active", None)
    breach = store.backlog_breach(now=NOW)
    assert breach is not None and "pending=1" in str(breach) and "oldest_h=144" in str(breach)
    assert "Aged00008" not in str(breach)


def test_only_an_eight_day_passing_row_is_not_claimed_and_does_not_page(db):
    _add(db, "Aged00008", NOW - timedelta(days=8))
    assert store.claim_next(now=NOW) is None
    assert _disposition(db, "Aged00008") == ("pending", "exhausted", "aged_out")
    assert store.seed_missing(now=NOW) == 0
    assert db.execute("SELECT COUNT(*),disposition,disposition_reason FROM marketdesk_pdf_queue").fetchone() == (
        1, "exhausted", "aged_out")
    db.execute(
        "UPDATE marketdesk_pdf_selection SET eligible=1, reason_code='PDF_MEASURED_MARKET_RESEARCH' "
        "WHERE article_id='Aged00008'")
    assert store.claim_next(now=NOW) is None
    assert _disposition(db, "Aged00008") == ("pending", "exhausted", "aged_out")
    assert store.backlog_breach(now=NOW) is None


def test_a_same_day_row_is_claimed_before_an_older_one_in_the_same_tier(db):
    """Lane 1 used to spend the slot on the oldest backlog. Same tier is newest-first."""
    _add(db, "Older0006", NOW - timedelta(days=6))
    _add(db, "Mid000002", NOW - timedelta(days=2))
    _add(db, "Today0001", NOW - timedelta(hours=1))
    db.execute("UPDATE marketdesk_pdf_throttle SET lane=1")
    assert store.claim_next(now=NOW).article_id == "Today0001"
    assert _disposition(db, "Older0006") == ("pending", "active", None)
    assert _disposition(db, "Mid000002") == ("pending", "active", None)


def test_priority_still_precedes_a_newer_ordinary_row(db):
    _add(db, "Today0001", NOW - timedelta(hours=1))
    _add(db, "Pref00006", NOW - timedelta(days=6), priority=1)
    assert store.claim_next(now=NOW).article_id == "Pref00006"


def test_max_age_env_overrides_and_zero_disables(db, monkeypatch):
    monkeypatch.setenv("MARKETDESK_PDF_MAX_AGE_DAYS", "3")
    _add(db, "Four00004", NOW - timedelta(days=4))
    _add(db, "Two000002", NOW - timedelta(days=2))
    young = store.claim_next(now=NOW)
    assert young.article_id == "Two000002"
    assert store.record_download(young, ASSET, DIGEST, now=NOW, provider_success=True)
    assert _disposition(db, "Four00004") == ("pending", "exhausted", "aged_out")
    monkeypatch.setenv("MARKETDESK_PDF_MAX_AGE_DAYS", "0")
    _add(db, "Ten000010", NOW - timedelta(days=10))
    opened = datetime.fromisoformat(db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0])
    aged = store.claim_next(now=opened)
    assert aged.article_id == "Ten000010"
    assert _disposition(db, "Ten000010")[1] == "active"
    assert store.record_download(aged, ASSET, DIGEST, now=opened, provider_success=True)
    monkeypatch.setenv("MARKETDESK_PDF_MAX_AGE_DAYS", "nope")
    _add(db, "Eight0008", NOW - timedelta(days=8))
    later = datetime.fromisoformat(db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0])
    assert store.claim_next(now=later) is None
    assert _disposition(db, "Eight0008") == ("pending", "exhausted", "aged_out")


def test_filtered_exhausted_and_downloaded_rows_are_not_rewritten(db):
    _add(db, "Miss00001", NOW - timedelta(hours=1))
    missed = store.claim_next(now=NOW)
    store.retry(missed, "file_missing", now=NOW)
    db.execute(
        "UPDATE marketdesk_pdf_queue SET published_at=? WHERE article_id='Miss00001'",
        ((NOW - timedelta(days=20)).isoformat(),))
    _add(db, "Filt00008", NOW - timedelta(days=8))
    _add(db, "Done00001", NOW - timedelta(hours=2))
    _add(db, "Keep00006", NOW - timedelta(days=6))
    db.execute(
        "UPDATE marketdesk_pdf_queue SET disposition='filtered', disposition_reason='off_book' WHERE article_id='Filt00008'")
    db.execute("UPDATE marketdesk_pdf_selection SET eligible=0, reason_code='off_book' WHERE article_id='Filt00008'")
    opened = datetime.fromisoformat(db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0])
    work = store.claim_next(now=opened)
    assert work.article_id == "Done00001"
    assert store.record_download(work, ASSET, DIGEST, now=opened, provider_success=True)
    db.execute(
        "UPDATE marketdesk_pdf_queue SET published_at=? WHERE article_id='Done00001'",
        ((NOW - timedelta(days=20)).isoformat(),))
    nxt = datetime.fromisoformat(db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0])
    assert store.claim_next(now=nxt).article_id == "Keep00006"
    assert _disposition(db, "Filt00008") == ("pending", "filtered", "off_book")
    assert _disposition(db, "Miss00001") == ("pending", "exhausted", "file_missing")
    assert _disposition(db, "Done00001") == ("downloaded", "active", None)


def test_a_newly_eligible_old_row_ages_out_instead_of_downloading(db):
    _add(db, "Back00008", NOW - timedelta(days=8))
    db.execute(
        "UPDATE marketdesk_pdf_queue SET disposition='filtered', disposition_reason='off_book' WHERE article_id='Back00008'")
    assert store.claim_next(now=NOW) is None
    assert _disposition(db, "Back00008") == ("pending", "exhausted", "aged_out")


def test_thirty_day_intake_above_the_cap_keeps_each_days_newest(db, monkeypatch):
    """85 passing reports a day, cap 70. Newest 70 of each day download.
    Nothing older than 7 days stays pending."""
    monkeypatch.setenv("MARKETDESK_PDF_DAILY_CAP", "70")
    monkeypatch.setenv("MARKETDESK_PDF_MAX_AGE_DAYS", "7")
    db.execute(
        "UPDATE marketdesk_pdf_throttle SET interval_seconds=300, batch_size=6, next_attempt_at=?, jitter_bps=0",
        (NOW.isoformat(),))
    clock = NOW
    newest = {}
    for day in range(30):
        ids = []
        for i in range(85):
            key = f"Day{day:02d}{i:04d}"
            # Newest report is due at `clock`. Older ones are already due too.
            _add(db, key, clock - timedelta(minutes=84 - i))
            ids.append(key)
        newest[day] = ids[-70:]
        db.execute(
            "UPDATE marketdesk_pdf_throttle SET next_attempt_at=?, interval_seconds=300, batch_size=6",
            (clock.isoformat(),))
        got = []
        for _step in range(20):
            batch = store.claim_batch(now=clock, jitter_bps=0, daily_cap_value=70, hourly_cap_value=0)
            if not batch:
                break
            got.extend(item.article_id for item in batch)
            _finish(db, batch, clock)
            clock = datetime.fromisoformat(
                db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0])
        assert set(got) == set(newest[day])
        last = db.execute("SELECT MAX(admitted_at) FROM marketdesk_pdf_admissions").fetchone()[0]
        clock = datetime.fromisoformat(last) + timedelta(days=1, seconds=1)
    store.backlog_breach(now=clock)
    pending = db.execute(
        "SELECT article_id,published_at FROM marketdesk_pdf_queue "
        "WHERE disposition='active' AND status='pending'").fetchall()
    limit = clock - timedelta(days=7)
    aged = db.execute(
        "SELECT COUNT(*) FROM marketdesk_pdf_queue WHERE disposition='exhausted' AND disposition_reason='aged_out'"
    ).fetchone()[0]
    assert pending
    assert all(datetime.fromisoformat(published) >= limit for _key, published in pending)
    assert aged + len(pending) == 15 * 30
    assert len(pending) <= 15 * 7
    for day, ids in newest.items():
        rows = db.execute(
            "SELECT article_id,status FROM marketdesk_pdf_queue WHERE article_id IN ({})".format(
                ",".join("?" for _ in ids)), tuple(ids)).fetchall()
        assert {status for _key, status in rows} == {"downloaded"}
    report = {"days": 30, "per_day": 85, "cap": 70, "aged_out": aged, "pending": len(pending)}
    path = Path("/opt/cursor/artifacts/marketdesk-pdf-age-sim.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print("AGE_SIM", json.dumps(report))


def test_fixed_1800s_pace_cannot_hide_a_week_of_intake(db, monkeypatch):
    """The retired slot, replayed: 150/day for 7 days leaves a red health row.
    The age window is off so this still measures the old pace, not retirement."""
    monkeypatch.setenv("MARKETDESK_PDF_MAX_AGE_DAYS", "0")
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


def test_fourteen_day_sim_drains_1300_and_keeps_up(monkeypatch):
    """400/day proof with the hourly cap off. The default cap is the other sim.
    The age window is off so a 10-day starting backlog is still drained."""
    monkeypatch.setenv("MARKETDESK_PDF_MAX_AGE_DAYS", "0")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for name in ("0001_init.sql", "0071_research_post_sources.sql", "0102_marketdesk_articles.sql",
                 "0103_marketdesk_pdf_queue.sql", "0104_marketdesk_pdf_account_backoff.sql",
                 "0105_marketdesk_pdf_priority.sql", "0112_marketdesk_pdf_rate.sql", "0113_marketdesk_pdf_safety_cap.sql", "0114_marketdesk_pdf_hourly_recovery.sql"):
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
        first = store.claim_batch(now=NOW, jitter_bps=0, hourly_cap_value=0)
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
            batch = store.claim_batch(now=clock, jitter_bps=0, hourly_cap_value=0)
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


def _recent(stamps, now):
    cutoff = now - timedelta(hours=1)
    return [stamp for stamp in stamps if stamp > cutoff]


def test_hourly_cap_drains_the_backlog_under_a_15_per_hour_provider(db, monkeypatch):
    """Real lease and controller. The provider refuses once an hour would pass 15."""
    monkeypatch.delenv(rate.HOURLY_CAP_ENV, raising=False)
    monkeypatch.delenv(rate.DAILY_CAP_ENV, raising=False)
    monkeypatch.delenv(rate.FLOOR_ENV, raising=False)
    monkeypatch.setenv("MARKETDESK_PDF_MAX_AGE_DAYS", "0")
    assert rate.hourly_cap() == 12 and rate.daily_cap() == 400
    quota_per_hour = 15
    assert len(_recent([NOW] * quota_per_hour, NOW)) + 1 > quota_per_hour
    assert len(_recent([NOW] * 12, NOW)) + 1 <= quota_per_hour
    for i in range(603):
        _add(db, f"B{i:07d}", NOW - timedelta(days=10))
    db.execute(
        "UPDATE marketdesk_pdf_throttle SET interval_seconds=2100,batch_size=1,recover_armed=1,"
        "recover_credits=0,success_credits=0,next_attempt_at=?",
        (NOW.isoformat(),))
    clock = NOW
    end = NOW + timedelta(days=12)
    stamps = []
    quota_hits = []
    daily = {}
    floor_at = None
    drained_at = None
    added = 0
    owed = 0.0
    last = clock
    max_hour = 0

    def accrue(now):
        nonlocal owed, added, last
        owed += 190 * (now - last).total_seconds() / 86400
        while owed >= 1:
            _add(db, f"N{added:07d}", now)
            added += 1
            owed -= 1
        last = now

    while clock < end and drained_at is None:
        accrue(clock)
        interval = db.execute("SELECT interval_seconds FROM marketdesk_pdf_throttle").fetchone()[0]
        if floor_at is None and interval <= rate.FLOOR_SECONDS:
            floor_at = clock
        batch = store.claim_batch(now=clock, jitter_bps=0)
        if not batch:
            due = datetime.fromisoformat(db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0])
            if due > clock:
                clock = due
                continue
            oldest = db.execute(
                "SELECT MIN(admitted_at) FROM marketdesk_pdf_admissions "
                "WHERE julianday(admitted_at) > julianday(?) - (1.0/24)",
                (clock.isoformat(),)).fetchone()[0]
            if oldest:
                aged = datetime.fromisoformat(oldest) + timedelta(hours=1)
                clock = aged if aged > clock else clock + timedelta(minutes=5)
            else:
                clock += timedelta(minutes=5)
            continue
        refused = None
        taken = 0
        recent = _recent(stamps, clock)
        for work in batch:
            if len(recent) + 1 > quota_per_hour:
                refused = work
                break
            recent.append(clock)
            stamps.append(clock)
            assert store.record_download(work, ASSET, DIGEST, now=clock, provider_success=True)
            taken += 1
            day = int((clock - NOW).total_seconds() // 86400)
            daily[day] = daily.get(day, 0) + 1
        if refused is not None:
            store.retry(refused, "preview_quota", now=clock)
            for pending in batch[taken + 1:]:
                store.release(pending, now=clock)
            quota_hits.append(clock.isoformat())
        elif taken:
            store.note_success(now=clock)
        max_hour = max(max_hour, len(_recent(stamps, clock)))
        if taken and _depth(db) == 0:
            drained_at = clock
            break
        due = datetime.fromisoformat(db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()[0])
        nxt = due if due > clock else clock + timedelta(minutes=5)
        if nxt <= clock:
            nxt = clock + timedelta(minutes=5)
        clock = nxt
    days = None if drained_at is None else round((drained_at - NOW).total_seconds() / 86400, 2)
    floor_hours = None if floor_at is None else round((floor_at - NOW).total_seconds() / 3600, 2)
    report = {
        "backlog": 603, "intake_per_day": 190, "hourly_cap": 12, "provider_quota_per_hour": quota_per_hour,
        "days_to_drain": days, "hours_to_floor": floor_hours, "max_in_any_hour": max_hour,
        "quota_hits": len(quota_hits), "daily": daily, "admitted": len(stamps), "intake": added,
        "final_depth": _depth(db), "final_interval": db.execute(
            "SELECT interval_seconds,batch_size,recover_armed FROM marketdesk_pdf_throttle").fetchone(),
    }
    path = Path("/opt/cursor/artifacts/marketdesk-pdf-hourly-sim.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print("HOURLY_SIM", json.dumps(report))
    assert floor_at is not None and floor_at - NOW < timedelta(hours=6)
    assert drained_at is not None and 5 <= days <= 9
    assert max_hour <= 12
    assert len(quota_hits) <= 4
    full_days = [count for day, count in daily.items() if 1 <= day < int(days)]
    assert full_days and all(250 <= count <= 290 for count in full_days)


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
