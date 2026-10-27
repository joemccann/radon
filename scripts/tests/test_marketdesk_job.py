"""One marketdesk cycle end to end: real ledger + publish SQL on sqlite, fakes for session, scrape, copy, Dropbox."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from db import writer as db_writer  # noqa: E402
from db.service_cycle import service_cycle  # noqa: E402
from research import marketdesk_job as job  # noqa: E402
from research import marketdesk_store as store  # noqa: E402
from research import publish  # noqa: E402
from research.assets import read_asset, store_asset  # noqa: E402

ROOT = SCRIPTS / "db/migrations"
CHARTS = "https://box1.nyc3.cdn.digitaloceanspaces.com/charts/"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


@dataclass(frozen=True)
class Ref:
    article_id: str
    url: str
    published_at: datetime
    title: str
    institution: str | None = None


@dataclass(frozen=True)
class Upload:
    path_lower: str
    id: str
    rev: str
    content_hash: str
    already_present: bool = False


def make_ref(article_id, hours_ago=1.0, title=None, institution="Acme"):
    return Ref(article_id, "https://marketdesk.ai/library/browse/" + article_id,
               NOW - timedelta(hours=hours_ago), title or f"Title {article_id}", institution)


class CopyUnavailable(RuntimeError):
    pass


class CopyOutage(CopyUnavailable):
    transient = True


class Killed(job.CycleTerminated):
    pass


@dataclass
class World:
    """Scriptable fake site + collaborators. Every call is logged for wire-level assertions."""
    tmp: Path
    refs: list = field(default_factory=list)
    no_pdf: set = field(default_factory=set)
    copy_fail: set = field(default_factory=set)
    copy_outage: set = field(default_factory=set)
    review_outage: set = field(default_factory=set)
    killed: set = field(default_factory=set)
    fetch_errors: dict = field(default_factory=dict)
    summary: list | None = None
    writer_error: Exception | None = None
    clock: list = field(default_factory=list)
    images: int = 2
    publisher: str | None = "Acme Research"
    auth_error: Exception | None = None
    list_error: Exception | None = None
    writer_present: bool = True
    upload_error: Exception | None = None
    upload_conflicts: int = 0
    connect_error: Exception | None = None
    folders: list | None = field(default_factory=lambda: ["2026", "October", "Oct 26", "Acme Research"])
    calls: list = field(default_factory=list)
    health: list = field(default_factory=list)
    uploads: list = field(default_factory=list)
    published: list = field(default_factory=list)
    dests: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    selections: dict = field(default_factory=dict)       # article title -> SELECT reply
    select_outage: set = field(default_factory=set)
    pages: dict = field(default_factory=dict)            # article id -> {page: text}
    prompts: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)
    feedback: list = field(default_factory=list)
    recent: list = field(default_factory=list)
    root: Path | None = None


class FakeWriter:
    def __init__(self, world):
        self.world = world

    def connect(self):
        self.world.calls.append(("connect",))
        if self.world.connect_error:
            raise self.world.connect_error

    def upload(self, local, remote):
        from research.dropbox import DropboxError
        data = Path(local).read_bytes()
        assert data.startswith(b"%PDF-")
        self.world.uploads.append((remote, data))
        if self.world.upload_error:
            raise self.world.upload_error
        if self.world.upload_conflicts:
            self.world.upload_conflicts -= 1
            raise DropboxError("Dropbox upload conflict", status=409)
        return Upload(remote.lower(), "id:" + Path(remote).stem, "rev1", "h" * 64)


def build_deps(world: World):
    def open_session(env):
        world.calls.append(("open_session",))
        if world.auth_error:
            raise world.auth_error
        session = SimpleNamespace(kind="session")
        world.sessions.append(session)
        return session

    def list_latest(session):
        assert session is world.sessions[-1]
        world.calls.append(("list_latest",))
        if world.list_error:
            raise world.list_error
        return list(world.refs)

    def fetch_article(session, ref):
        assert session is world.sessions[-1]
        world.calls.append(("fetch_article", ref.article_id))
        if ref.article_id in world.fetch_errors:
            raise world.fetch_errors[ref.article_id]
        summary = ["Fed priced two cuts — curve steepened", "x" * 700] if world.summary is None else world.summary
        return SimpleNamespace(ref=ref, title=ref.title, publisher=world.publisher, summary=summary,
                               image_urls=[CHARTS + "a.png"], folders=world.folders)

    def download_pdf(session, article, dest):
        assert session is world.sessions[-1]
        world.dests.append(Path(dest))
        if article.ref.article_id in world.no_pdf:
            raise RuntimeError("no_pdf")
        Path(dest).mkdir(parents=True, exist_ok=True)
        path = Path(dest) / "source.pdf"
        path.write_bytes(b"%PDF-1.7 " + article.ref.article_id.encode())
        return path

    def download_images(session, article, dest, *, limit=40):
        Path(dest).mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(min(world.images, limit)):
            path = Path(dest) / f"img{index}.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + f"{article.ref.article_id}-{index}".encode())
            paths.append(path)
        world.calls.append(("download_images", limit))
        return paths

    def write_copy(*, title, publisher, summary, body_text, images, finding=None):
        image_ids = [label for label, _ in images]
        world.calls.append(("copy_finding", (finding or {}).get("title")))
        assert all(Path(path).read_bytes().startswith(b"\x89PNG") for _, path in images)
        world.calls.append(("write_copy", title, publisher, tuple(image_ids)))
        world.calls.append(("copy_summary", tuple(summary)))
        world.calls.append(("copy_body", body_text))
        if title in world.killed:
            raise Killed("SIGTERM")
        if title in world.copy_fail:
            raise CopyUnavailable("ladder exhausted")
        if title in world.copy_outage:
            raise CopyOutage("Model ladder exhausted after trying every keyed provider. tried=anthropic:http_529")
        return SimpleNamespace(title="Voice " + title, content="Two cuts priced, curve up 12bp.",
                               captions={i: f"caption {i}" for i in image_ids}, tags=["RATES", "MARKETDESK", "FED"],
                               provider="fake", model="fake-1", source_text="Two cuts priced, curve up 12bp.",
                               image_ids=tuple(image_ids))

    def certify(post, copy):
        from clients.model_ladder import LadderResult, ModelLadderExhausted
        from research import marketdesk_copy
        world.calls.append(("certify", post["id"]))

        def complete(instruction, **kwargs):
            if post["title"] in world.review_outage:
                raise ModelLadderExhausted("Model ladder exhausted. tried=anthropic:http_529")
            return LadderResult(data={"findings": [], "qualitative": []}, text="{}", provider="fake", model="fake-1")

        return marketdesk_copy.certify(post, copy, complete=complete)

    def real_publish(post):
        world.published.append(post)
        return publish.publish(post)

    def marketdesk_path(published_at, title, article_id, root="/Radon/MarketDesk", folders=None, suffix_id=False):
        name = title + (f" ({article_id})" if suffix_id else "")
        if folders:
            return "/".join([root, *folders, name + ".pdf"])
        return f"{root}/{published_at:%Y}/{article_id}.pdf"

    def record_health(service, state, *, started_at=None, finished_at=None, error=None):
        world.health.append((service, state, error))

    def expire():
        world.calls.append(("expire_stale_held",))
        return 0

    def writer_from_env(env):
        if world.writer_error:
            raise world.writer_error
        return FakeWriter(world) if world.writer_present else None

    def clock():
        return world.clock.pop(0) if world.clock else 0.0

    def pdf_pages(path):
        data = Path(path).read_bytes()
        assert data.startswith(b"%PDF-")
        world.calls.append(("pdf_pages", Path(path).name))
        article_id = data[len(b"%PDF-1.7 "):].decode()
        return world.pages.get(article_id, {1: "PDF text: EBITDA $13.80B, curve up 12bp."}), None

    def select(prompt):
        world.prompts.append(prompt)
        title = next((t for t in world.selections if json.dumps(t + ".pdf") in prompt), None)
        if any(json.dumps(t + ".pdf") in prompt for t in world.select_outage):
            from research.marketdesk_filter import SelectUnavailable
            raise SelectUnavailable("Model ladder exhausted. tried=codex:http_529")
        if title is not None:
            return world.selections[title]
        return {"candidates": [{"title": "Finding", "content": "Two cuts priced.", "claim_key": "k", "pages": [1],
                                "figure_ids": [], "captions": {}, "tags": ["RATES"], "text_only": True}]}

    def record_outcome(work, review):
        world.outcomes.append((work, review))
        return publish.record_outcome(work, review)

    return job.Deps(
        store=store, publish=real_publish, store_asset=store_asset, read_asset=read_asset,
        expire_stale_held=expire, open_session=open_session, list_latest=list_latest, fetch_article=fetch_article,
        download_pdf=download_pdf, download_images=download_images, write_copy=write_copy,
        certify=certify, writer_from_env=writer_from_env, pdf_pages=pdf_pages, select=select,
        book_tickers=lambda: None, recent_titles=lambda: list(world.recent), record_outcome=record_outcome,
        feedback_rows=lambda: list(world.feedback), research_root=lambda: world.root or world.tmp / "research",
        marketdesk_path=marketdesk_path, service_cycle=service_cycle, record_health=record_health,
        clock=clock,
    )


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("RADON_RESEARCH_DIR", str(tmp_path / "research"))
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for name in ("0001_init.sql", "0071_research_post_sources.sql", "0078_research_feedback.sql",
                 "0079_research_outcomes.sql", "0080_research_rule_proposals.sql", "0082_research_outcomes_context.sql",
                 "0091_research_outcomes_hold_times.sql", "0102_marketdesk_articles.sql"):
        conn.executescript((ROOT / name).read_text())

    def execute(sql, args=(), timeout=4.0):
        return [tuple(r) for r in conn.execute(sql, tuple(args)).fetchall()]

    def transaction(statements, timeout=4.0):
        conn.execute("BEGIN")
        try:
            for sql, args in statements:
                conn.execute(sql, tuple(args))
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    monkeypatch.setattr(store, "hrana_execute", execute)
    monkeypatch.setattr(publish, "hrana_transaction", transaction)
    monkeypatch.setattr(publish, "hrana_execute", execute)
    from api import db_http
    monkeypatch.setattr(db_http, "hrana_execute", execute)  # research.learn.sync resolves it at call time
    yield conn
    conn.close()


@pytest.fixture
def world(tmp_path, monkeypatch, db):
    w = World(tmp=tmp_path)
    monkeypatch.setattr(db_writer, "record_service_health",
                        lambda service, state, **kw: w.health.append((service, state, kw.get("error"))))
    monkeypatch.setattr(db_writer, "ensure_no_replica_for_writers", lambda: None, raising=False)
    return w


ENV = {"MARKETDESK_MAX_PER_CYCLE": "5", "MARKETDESK_LOOKBACK_HOURS": "48"}


def run(world, env=None):
    return job.run_cycle(env={**ENV, **(env or {})}, deps=build_deps(world))


def ledger(db, article_id):
    cur = db.execute("SELECT * FROM marketdesk_articles WHERE article_id=?", (article_id,))
    values = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], values)) if values else None


def health_for(world, service):
    return [h for h in world.health if h[0] == service]


def test_new_article_published_once_with_article_timestamp(world, db):
    ref = make_ref("4AJs5Adl2Yp", hours_ago=2, title="Fed cuts")
    world.refs = [ref]
    counts = run(world)
    assert counts["published"] == 1 and counts["uploaded"] == 1
    post_id = publish.stable_post_id("marketdesk:4AJs5Adl2Yp", "article")
    row = db.execute("SELECT id, title, content, timestamp, images, tags FROM posts").fetchone()
    assert row[0] == post_id and row[1] == "Voice Fed cuts"
    assert datetime.fromisoformat(row[3]) == ref.published_at
    tags = json.loads(row[5])
    assert tags == ["MARKETDESK", "RATES", "FED"]
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert source["kind"] == "marketdesk" and source["articleId"] == "4AJs5Adl2Yp"
    assert source["articleUrl"] == "https://marketdesk.ai/library/browse/4AJs5Adl2Yp"
    assert source["publisher"] == "Acme Research"
    assert source["url"].endswith(source["contentHash"] + ".pdf")
    assert source["pages"] == [1]
    assert [f["caption"] for f in source["figures"]] == ["caption i1", "caption i2"]
    assert json.loads(row[4]) == [f["url"] for f in source["figures"]]
    assert all("—" not in s and len(s) <= 500 for s in source["summary"])
    assert source["extraImages"] == []
    assert source["summary"] == ["Fed priced two cuts, curve steepened", "x" * 500]
    saved = ledger(db, "4AJs5Adl2Yp")
    assert saved["status"] == "published" and saved["post_id"] == post_id
    assert saved["pdf_asset"] == source["url"] and saved["pdf_sha256"] == source["contentHash"]
    assert saved["dropbox_status"] == "uploaded"
    assert world.uploads[0][0] == "/Radon/MarketDesk/2026/October/Oct 26/Acme Research/Fed cuts.pdf"
    assert source["folders"] == ["2026", "October", "Oct 26", "Acme Research"]
    assert source["fileName"] == "Fed cuts"
    assert ("write_copy", "Fed cuts", "Acme Research", ("i1", "i2")) in world.calls
    assert ("marketdesk-research", "ok", None) in world.health
    assert ("marketdesk-dropbox", "ok", None) in world.health
    assert ("expire_stale_held",) in world.calls

    world.calls.clear()
    world.uploads.clear()
    world.published.clear()
    counts = run(world)
    assert counts["published"] == 0
    assert world.published == [] and world.uploads == []
    assert not [c for c in world.calls if c[0] == "fetch_article"]
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 1


def test_images_beyond_twelve_become_extra_images(world, db):
    world.refs = [make_ref("Bbbbbbbb22")]
    world.images = 15
    run(world)
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert len(source["figures"]) == 12 and len(source["extraImages"]) == 3
    assert ("download_images", 40) in world.calls
    assert [c for c in world.calls if c[0] == "write_copy"][0][3] == tuple(f"i{n}" for n in range(1, 13))


def test_forbidden_or_missing_publisher_falls_back_to_marketdesk(world, db):
    world.refs = [make_ref("Cccccccc33")]
    world.publisher = "The Market Ear"
    run(world)
    world.publisher = None
    world.refs = [make_ref("Dddddddd44")]
    run(world)
    publishers = [json.loads(r[0])["publisher"] for r in db.execute("SELECT provenance_json FROM research_post_sources")]
    assert publishers == ["MarketDesk", "MarketDesk"]
    assert [c[2] for c in world.calls if c[0] == "write_copy"] == [None, None]


def test_crash_heal_records_without_republishing(world, db):
    ref = make_ref("Eeeeeeee55")
    world.refs = [ref]
    run(world)
    db.execute("UPDATE marketdesk_articles SET status='pending', post_id=NULL, pdf_asset=NULL, pdf_sha256=NULL, "
               "dropbox_status=NULL WHERE article_id=?", (ref.article_id,))
    world.published.clear(); world.calls.clear(); world.uploads.clear()
    counts = run(world)
    assert counts["healed"] == 1 and counts["published"] == 0
    assert world.published == []
    assert not [c for c in world.calls if c[0] in ("fetch_article", "write_copy")]
    saved = ledger(db, ref.article_id)
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert saved["status"] == "published" and saved["pdf_asset"] == source["url"]
    assert saved["pdf_sha256"] == source["contentHash"]
    assert len(world.uploads) == 1


def test_no_pdf_recorded_as_failure_and_retired_after_five(world, db):
    world.refs = [make_ref("Ffffffff66")]
    world.no_pdf = {"Ffffffff66"}
    with pytest.raises(job.ArticlesFailed):
        run(world)
    saved = ledger(db, "Ffffffff66")
    assert saved["status"] == "pending" and saved["attempts"] == 1 and saved["last_error"] == "no_pdf"
    for _ in range(4):
        with pytest.raises(job.ArticlesFailed):
            run(world)
    assert ledger(db, "Ffffffff66")["status"] == "failed"
    world.calls.clear()
    world.health.clear()
    run(world)
    assert not [c for c in world.calls if c[0] == "fetch_article"]
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0
    assert ("marketdesk-research", "ok", None) in world.health


def test_one_bad_article_does_not_block_others(world, db):
    world.refs = [make_ref("Gggggggg77", title="Bad"), make_ref("Hhhhhhhh88", title="Good")]
    world.copy_fail = {"Bad"}
    counts = run(world)
    assert counts == {**counts, "published": 1, "failed": 1}
    assert ledger(db, "Gggggggg77")["last_error"] == "ladder exhausted"
    assert ledger(db, "Hhhhhhhh88")["status"] == "published"


def test_writer_absent_publishes_skips_upload_and_pauses_health(world, db):
    world.refs = [make_ref("Iiiiiiii99")]
    world.writer_present = False
    counts = run(world)
    assert counts["published"] == 1 and counts["upload_skipped"] == 1
    assert ledger(db, "Iiiiiiii99")["dropbox_status"] == "skipped_no_creds"
    dropbox = health_for(world, "marketdesk-dropbox")
    assert dropbox == [("marketdesk-dropbox", "paused",
                        {"message": "Dropbox write credentials absent; uploads skipped"})]
    # Creds arrive later: the skipped row is retried.
    world.writer_present = True
    world.health.clear()
    counts = run(world)
    assert counts["uploaded"] == 1 and ledger(db, "Iiiiiiii99")["dropbox_status"] == "uploaded"
    assert health_for(world, "marketdesk-dropbox") == [("marketdesk-dropbox", "ok", None)]


def test_upload_failure_errors_health_and_retries_next_cycle(world, db):
    from research.dropbox import DropboxError
    world.refs = [make_ref("Jjjjjjjj10")]
    world.upload_error = DropboxError("Dropbox request failed (503)")
    counts = run(world)
    assert counts["published"] == 1 and counts["upload_failed"] == 1
    saved = ledger(db, "Jjjjjjjj10")
    assert saved["dropbox_status"] == "failed" and "503" in saved["last_error"]
    service, state, error = health_for(world, "marketdesk-dropbox")[0]
    assert state == "error" and "1 upload" in error["message"]
    assert ("marketdesk-research", "ok", None) in world.health
    world.upload_error = None
    world.health.clear()
    counts = run(world)
    assert counts["uploaded"] == 1 and ledger(db, "Jjjjjjjj10")["dropbox_status"] == "uploaded"
    assert health_for(world, "marketdesk-dropbox")[0][1] == "ok"


def test_connect_failure_leaves_rows_and_errors_health(world, db):
    from research.dropbox import DropboxError
    world.refs = [make_ref("Kkkkkkkk11")]
    world.connect_error = DropboxError("Dropbox account mismatch")
    run(world)
    assert ledger(db, "Kkkkkkkk11")["dropbox_status"] is None
    assert health_for(world, "marketdesk-dropbox")[0][1] == "error"
    assert world.uploads == []


def test_auth_failure_error_row_and_reraise_but_outbox_still_runs(world, db):
    world.refs = [make_ref("Llllllll12")]
    run(world)
    db.execute("UPDATE marketdesk_articles SET dropbox_status=NULL")
    world.auth_error = RuntimeError("marketdesk session expired")
    world.health.clear(); world.uploads.clear()
    with pytest.raises(RuntimeError, match="session expired"):
        run(world)
    research = health_for(world, "marketdesk-research")
    assert research[0][1] == "error" and "session expired" in research[0][2]["message"]
    assert len(world.uploads) == 1
    assert health_for(world, "marketdesk-dropbox")[0][1] == "ok"


def test_listing_failure_raises(world, db):
    world.list_error = RuntimeError("library selector missing")
    with pytest.raises(RuntimeError):
        run(world)
    assert health_for(world, "marketdesk-research")[0][1] == "error"


def test_per_cycle_cap_and_lookback(world, db):
    world.refs = [make_ref(f"Mmmmmmm{n:03d}", hours_ago=n / 10) for n in range(1, 8)]
    world.refs.append(make_ref("Oldoldold1", hours_ago=72))
    counts = run(world, {"MARKETDESK_MAX_PER_CYCLE": "3"})
    assert counts["published"] == 3
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 3
    run(world, {"MARKETDESK_MAX_PER_CYCLE": "3"})
    run(world, {"MARKETDESK_MAX_PER_CYCLE": "3"})
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 7
    assert ledger(db, "Oldoldold1") is None


def test_invalid_env_bounds_fall_back_to_defaults(world, db):
    world.refs = [make_ref(f"Nnnnnnn{n:03d}") for n in range(1, 8)]
    counts = run(world, {"MARKETDESK_MAX_PER_CYCLE": "lots", "MARKETDESK_LOOKBACK_HOURS": "-1"})
    assert counts["published"] == 5


def test_expire_stale_held_failure_is_best_effort(world, db):
    deps = build_deps(world)
    deps.expire_stale_held = lambda: (_ for _ in ()).throw(RuntimeError("turso slow"))
    world.refs = [make_ref("Pppppppp13")]
    counts = job.run_cycle(env=ENV, deps=deps)
    assert counts["published"] == 1


def test_workdir_removed_and_one_session_per_cycle(world, db):
    world.refs = [make_ref("Qqqqqqqq14"), make_ref("Qqqqqqqq15")]
    run(world)
    assert world.dests and all(not d.exists() for d in world.dests)
    assert [c for c in world.calls if c[0] == "open_session"] == [("open_session",)]
    world.refs = []
    assert run(world)["published"] == 0
    assert len(world.sessions) == 2


def test_workdir_removed_even_when_cycle_raises(world, db, monkeypatch):
    created = []
    real = job.tempfile.mkdtemp
    monkeypatch.setattr(job.tempfile, "mkdtemp", lambda **kw: created.append(real(**kw)) or created[-1])
    world.auth_error = RuntimeError("expired")
    with pytest.raises(RuntimeError):
        run(world)
    assert created and not Path(created[0]).exists()


def test_main_returns_nonzero_on_failure_and_zero_on_success(monkeypatch, capsys):
    monkeypatch.setattr(job, "run_cycle", lambda **kw: {"published": 2})
    assert job.main() == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["published"] == 2
    monkeypatch.setattr(job, "run_cycle", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert job.main() == 1


def test_runnable_by_path_imports_without_side_effects():
    code = ("import runpy, sys; sys.argv=['marketdesk_job.py','--help'];"
            f"runpy.run_path({str(SCRIPTS / 'research/marketdesk_job.py')!r}, run_name='not_main')")
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_default_deps_wire_real_modules():
    deps = job.default_deps()
    from research import dropbox_writer, marketdesk_auth, marketdesk_copy, marketdesk_scrape
    assert deps.store is store and deps.publish is publish.publish
    assert deps.open_session is marketdesk_auth.open_session
    assert deps.fetch_article is marketdesk_scrape.fetch_article
    from research import book, marketdesk_filter, pipeline
    assert deps.pdf_pages is pipeline.pages_isolated
    assert deps.select is marketdesk_filter.ladder_select
    assert deps.book_tickers is book.tickers and deps.record_outcome is publish.record_outcome
    assert deps.list_latest is marketdesk_scrape.list_latest and deps.download_pdf is marketdesk_scrape.download_pdf
    assert deps.write_copy is marketdesk_copy.write_copy
    assert deps.certify is marketdesk_copy.certify
    assert deps.writer_from_env({}) is None
    assert deps.marketdesk_path is dropbox_writer.marketdesk_path


# ---- review fixes: transient parking, lease, health, gap, summary, budget, secrets ------------

def research_errors(world):
    return [h[2]["message"] for h in health_for(world, "marketdesk-research") if h[1] == "error"]


def test_every_article_failing_turns_cycle_health_red(world, db):
    world.refs = [make_ref("Rrrrrrrr01", title="A"), make_ref("Rrrrrrrr02", title="B")]
    world.no_pdf = {"Rrrrrrrr01", "Rrrrrrrr02"}
    with pytest.raises(job.ArticlesFailed, match="2 of 2 articles failed"):
        run(world)
    assert research_errors(world) and "no_pdf" in research_errors(world)[0]
    assert health_for(world, "marketdesk-dropbox")[0][1] == "ok"


def test_retirement_turns_health_red_even_when_another_article_publishes(world, db):
    bad = make_ref("Ssssssss01", title="Bad")
    world.refs = [bad]
    world.no_pdf = {bad.article_id}
    for _ in range(4):
        with pytest.raises(job.ArticlesFailed):
            run(world)
    world.refs = [bad, make_ref("Ssssssss02", title="Good")]
    with pytest.raises(job.ArticlesFailed, match="retired"):
        run(world)
    assert ledger(db, "Ssssssss02")["status"] == "published"
    assert ledger(db, bad.article_id)["status"] == "failed"


def test_model_outage_parks_without_spending_attempts(world, db):
    world.refs = [make_ref("Tttttttt01", title="Outage")]
    world.copy_outage = {"Outage"}
    for _ in range(8):
        with pytest.raises(job.ArticlesFailed):
            run(world)
    saved = ledger(db, "Tttttttt01")
    assert saved["status"] == "pending" and saved["attempts"] == 0
    assert "ladder exhausted" in saved["last_error"]
    world.copy_outage = set()
    world.health.clear()
    assert run(world)["published"] == 1
    assert ("marketdesk-research", "ok", None) in world.health


@pytest.mark.parametrize("code", ["http_503", "http_429", "http_transport", "http_500"])
def test_transient_scrape_errors_park(world, db, code):
    from research.marketdesk_scrape import ScrapeError
    world.refs = [make_ref("Uuuuuuuu01")]
    world.fetch_errors = {"Uuuuuuuu01": ScrapeError(code)}
    for _ in range(6):
        with pytest.raises(job.ArticlesFailed):
            run(world)
    saved = ledger(db, "Uuuuuuuu01")
    assert saved["status"] == "pending" and saved["attempts"] == 0 and saved["last_error"] == code


def test_session_expiry_mid_cycle_parks_and_fails_the_cycle(world, db):
    from research.marketdesk_scrape import ScrapeError
    world.refs = [make_ref("Vvvvvvvv01", hours_ago=1), make_ref("Vvvvvvvv02", hours_ago=2)]
    world.fetch_errors = {"Vvvvvvvv01": ScrapeError("unauthenticated")}
    with pytest.raises(ScrapeError, match="unauthenticated"):
        run(world)
    assert ledger(db, "Vvvvvvvv01")["attempts"] == 0
    assert ledger(db, "Vvvvvvvv02") is None  # the cycle stopped; nothing else spent an attempt
    assert research_errors(world)


def test_killed_article_spends_an_attempt_and_outbox_still_drains(world, db):
    world.refs = [make_ref("Wwwwwwww01", hours_ago=3, title="Done")]
    world.writer_present = False
    run(world)
    world.writer_present = True
    poison = make_ref("Wwwwwwww02", hours_ago=1, title="Poison")
    world.refs = [poison]
    world.killed = {"Poison"}
    world.health.clear()
    with pytest.raises(job.CycleTerminated):
        run(world)
    assert ledger(db, "Wwwwwwww01")["dropbox_status"] == "uploaded"  # outbox ran before the scrape
    assert health_for(world, "marketdesk-dropbox")[0][1] == "ok"
    assert research_errors(world)
    assert ledger(db, poison.article_id)["attempts"] == 0  # leased, counted at the next claim
    for _ in range(4):
        with pytest.raises(job.CycleTerminated):
            run(world)
    assert ledger(db, poison.article_id)["attempts"] == 4
    run(world)
    saved = ledger(db, poison.article_id)
    assert saved["status"] == "failed" and saved["attempts"] == 5 and "interrupted" in saved["last_error"]


def test_article_start_budget_defers_remaining_articles(world, db):
    world.refs = [make_ref(f"Xxxxxxx{n:03d}", hours_ago=n / 10) for n in range(1, 4)]
    world.clock = [0.0, 1.0, job.ARTICLE_START_BUDGET_SECONDS + 1, job.ARTICLE_START_BUDGET_SECONDS + 2]
    counts = run(world)
    assert counts["published"] == 1 and counts["deferred"] == 2
    assert ledger(db, "Xxxxxxx002") is None


def test_listing_gap_is_reported_and_self_clears(world, db):
    world.refs = [make_ref("Yyyyyyyy01", hours_ago=10)]
    run(world)
    world.refs = [make_ref("Yyyyyyyy02", hours_ago=2), make_ref("Yyyyyyyy03", hours_ago=1)]
    with pytest.raises(job.ListingGap):
        run(world)
    assert ledger(db, "Yyyyyyyy02")["status"] == "published"
    assert any("listing_gap" in m for m in research_errors(world))
    world.health.clear()
    run(world)
    assert ("marketdesk-research", "ok", None) in world.health


def test_unprocessed_article_waits_for_its_summary_without_spending_attempts(world, db):
    world.refs = [make_ref("Zzzzzzzz01", hours_ago=0.2)]
    world.summary = []
    for _ in range(3):
        counts = run(world)
        assert counts["pending"] == 1 and counts["published"] == 0
    saved = ledger(db, "Zzzzzzzz01")
    assert saved["status"] == "pending" and saved["attempts"] == 0 and saved["last_error"] == "summary_pending"
    assert world.dests == [] and not [c for c in world.calls if c[0] == "write_copy"]
    assert all(h[1] == "ok" for h in health_for(world, "marketdesk-research"))
    world.summary = None  # MarketDesk finished processing it
    assert run(world)["published"] == 1


def test_summary_that_never_arrives_fails_after_the_wait(world, db):
    world.refs = [make_ref("Zzzzzzzz07", hours_ago=job.DEFAULT_SUMMARY_WAIT_HOURS + 1)]
    world.summary = []
    with pytest.raises(job.ArticlesFailed):
        run(world)
    assert ledger(db, "Zzzzzzzz07")["last_error"] == "no_summary"
    assert world.dests == []


def test_pending_articles_do_not_use_up_the_per_cycle_cap(world, db):
    world.refs = [make_ref(f"Pend{n:07d}", hours_ago=0.1) for n in range(4)]
    world.refs += [make_ref("Ready000001", hours_ago=2), make_ref("Ready000002", hours_ago=3)]
    pending = {r.article_id for r in world.refs[:4]}
    original = build_deps

    def deps_with_pending(w):
        deps = original(w)
        fetch = deps.fetch_article

        def fetch_article(session, ref):
            article = fetch(session, ref)
            return SimpleNamespace(**{**vars(article), "summary": [] if ref.article_id in pending else article.summary})

        deps.fetch_article = fetch_article
        return deps

    counts = job.run_cycle(env={**ENV, "MARKETDESK_MAX_PER_CYCLE": "2"}, deps=deps_with_pending(world))
    assert counts["pending"] == 4 and counts["published"] == 2


def test_market_ear_items_are_excluded_before_any_claim(world, db):
    world.refs = [make_ref("Earearear01", institution="The Market Ear"), make_ref("Okokokok01")]
    counts = run(world)
    assert counts["excluded"] == 1 and counts["published"] == 1
    assert ledger(db, "Earearear01") is None
    assert ("fetch_article", "Earearear01") not in world.calls


def test_dropbox_name_collision_retries_once_with_the_article_id(world, db):
    world.refs = [make_ref("Coll000001", title="Tesla Oct 26")]
    world.upload_conflicts = 1
    counts = run(world)
    assert counts["uploaded"] == 1
    assert [u[0] for u in world.uploads] == [
        "/Radon/MarketDesk/2026/October/Oct 26/Acme Research/Tesla Oct 26.pdf",
        "/Radon/MarketDesk/2026/October/Oct 26/Acme Research/Tesla Oct 26 (Coll000001).pdf"]


def test_outbox_falls_back_to_the_dated_layout_without_folders(world, db):
    world.refs = [make_ref("Nofold0001")]
    world.folders = []
    run(world)
    assert world.uploads[0][0].endswith("/Nofold0001.pdf")


def test_summary_bullets_are_filtered_before_copy_and_publish(world, db):
    world.refs = [make_ref("Zzzzzzzz02")]
    world.summary = [
        "The Market Ear flags CTA selling of $30bn",
        "Per ZeroHedge, desks are short",
        "See https://example.com/x for the chart",
        "Positioning \u201ctight\u201d \u2014 gamma short",
        "In conclusion, it remains to be seen",
        "Gamma flips at 5800",
    ]
    run(world)
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert source["summary"] == ['Positioning "tight", gamma short', "Gamma flips at 5800"]
    assert ("copy_summary", tuple(source["summary"])) in world.calls


def test_summary_cut_is_utf16_safe(world, db):
    world.refs = [make_ref("Zzzzzzzz03")]
    world.summary = ["a" * 499 + "\U0001F4C8" + "b" * 10]
    run(world)
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    bullet = source["summary"][0]
    assert len(bullet.encode("utf-16-le")) // 2 <= 500


def test_copy_is_grounded_in_the_pdf_text(world, db):
    world.refs = [make_ref("Pdftext001")]
    run(world)
    assert ("pdf_pages", "source.pdf") in world.calls
    assert ("copy_body", "PDF text: EBITDA $13.80B, curve up 12bp.") in world.calls


def test_copy_sees_the_figure_images(world, db):
    world.refs = [make_ref("Zzzzzzzz04")]
    world.images = 3
    run(world)
    assert [c for c in world.calls if c[0] == "write_copy"][0][3] == ("i1", "i2", "i3")


def test_incomplete_dropbox_creds_error_health_without_touching_rows(world, db):
    from research.dropbox import DropboxError
    world.refs = [make_ref("Zzzzzzzz05")]
    world.writer_error = DropboxError("Dropbox write credentials incomplete: missing DROPBOX_WRITE_ACCOUNT_ID")
    counts = run(world)
    assert counts["published"] == 1
    assert ledger(db, "Zzzzzzzz05")["dropbox_status"] is None
    state, error = health_for(world, "marketdesk-dropbox")[0][1:]
    assert state == "error" and "incomplete" in error["message"]
    assert ("marketdesk-research", "ok", None) in world.health


SECRET_LOG = ("APIRequestContext.post: Timeout 2000ms exceeded.\nCall log:\n  - -> POST https://marketdesk.ai/x\n"
              "  - cookie: __session=OLDSECRET\n  - <- 200 OK\n    - set-cookie: session=NEWSECRET")


def test_cycle_error_never_carries_a_playwright_call_log(world, db):
    world.auth_error = RuntimeError(SECRET_LOG)
    with pytest.raises(RuntimeError) as info:
        run(world)
    assert "SECRET" not in str(info.value)
    assert research_errors(world) and all("SECRET" not in m and "Call log" not in m for m in research_errors(world))


def test_article_error_never_stores_a_playwright_call_log(world, db):
    world.refs = [make_ref("Zzzzzzzz06")]
    world.fetch_errors = {"Zzzzzzzz06": RuntimeError(SECRET_LOG)}
    with pytest.raises(job.ArticlesFailed) as info:
        run(world)
    assert "SECRET" not in ledger(db, "Zzzzzzzz06")["last_error"]
    assert "SECRET" not in str(info.value)


def test_main_installs_a_sigterm_handler_for_the_cycle(monkeypatch):
    import signal
    seen = {}

    def fake_cycle(**kw):
        handler = signal.getsignal(signal.SIGTERM)
        seen["handler"] = handler
        with pytest.raises(job.CycleTerminated):
            handler(signal.SIGTERM, None)
        return {}

    before = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(job, "run_cycle", fake_cycle)
    assert job.main() == 0
    assert callable(seen["handler"]) and seen["handler"] is not before
    assert signal.getsignal(signal.SIGTERM) is before


# ---- Humanizer certificate (research.publish refuses research posts without one) -------------

def test_published_post_carries_humanizer_certificate_kept_out_of_provenance(world, db):
    from research import humanizer
    world.refs = [make_ref("Hhhhhhhh01", title="Certified")]
    assert run(world)["published"] == 1
    post = world.published[0]
    humanizer.validate_certificate(post)
    assert ("certify", post["id"]) in world.calls
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert "humanizer" not in source


def test_humanizer_review_outage_parks_without_spending_attempts(world, db):
    world.refs = [make_ref("Hhhhhhhh02", title="Reviewless")]
    world.review_outage = {"Voice Reviewless"}
    with pytest.raises(job.ArticlesFailed):
        run(world)
    saved = ledger(db, "Hhhhhhhh02")
    assert saved["status"] == "pending" and saved["attempts"] == 0
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


# ---- relevance filter: the intake v2 gates before any copy call ------------------------------------------

def outcome_row(db, article_id):
    cur = db.execute("SELECT * FROM research_outcomes WHERE work_key=?", ("marketdesk:" + article_id,))
    values = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], values)) if values else None


def test_empty_selection_is_held_never_copied_and_still_saved_to_dropbox(world, db):
    world.refs = [make_ref("Held000001", title="Routine recap")]
    world.selections = {"Routine recap": {"candidates": [], "reason": "no new measured evidence"}}
    counts = run(world)
    assert counts["held"] == 1 and counts["published"] == 0
    assert not [c for c in world.calls if c[0] in ("write_copy", "download_images")]
    assert world.published == []
    saved = ledger(db, "Held000001")
    assert saved["status"] == "held" and saved["reason_code"] == "NO_CANDIDATES"
    assert saved["dropbox_status"] == "uploaded"
    assert world.uploads[0][0] == "/Radon/MarketDesk/2026/October/Oct 26/Acme Research/Routine recap.pdf"
    out = outcome_row(db, "Held000001")
    assert out["outcome"] == "held" and json.loads(out["reason_codes"]) == ["NO_CANDIDATES"]
    assert out["pipeline"] == "marketdesk" and out["publisher"] == "Acme Research"
    assert out["file_id"] == "Held000001" and out["file_name"] == "Routine recap.pdf"
    assert json.loads(out["context_json"])["sourceUrl"] == saved["pdf_asset"]
    assert ("marketdesk-research", "ok", None) in world.health
    assert run(world)["held"] == 0  # terminal: never re-reviewed from the listing


def test_triage_drop_skips_the_model_entirely(world, db):
    world.refs = [make_ref("Drop000001", title="EURUSD note")]
    world.pages = {"Drop000001": {1: "# EURUSD: Range trade into the ECB"}}
    counts = run(world)
    assert counts["dropped"] == 1
    assert world.prompts == [] and not [c for c in world.calls if c[0] == "write_copy"]
    assert ledger(db, "Drop000001")["reason_code"] == "DOC_TYPE_FX_PAIR_NOTE"
    out = outcome_row(db, "Drop000001")
    assert out["outcome"] == "dropped" and json.loads(out["reason_codes"]) == ["DOC_TYPE_FX_PAIR_NOTE"]


def test_unknown_publisher_is_held(world, db):
    world.refs = [make_ref("Unkn000001", title="Anon note")]
    world.folders = ["2026", "October", "Oct 26"]
    counts = run(world)
    assert counts["held"] == 1 and world.prompts == []
    assert ledger(db, "Unkn000001")["reason_code"] == "PUBLISHER_UNKNOWN"


def test_duplicate_of_a_published_article_is_dropped(world, db):
    text = {1: " ".join(f"word{n} flows positioning" for n in range(60))}
    world.refs = [make_ref("Dupe000001", title="First", hours_ago=2), make_ref("Dupe000002", title="Second", hours_ago=1)]
    world.pages = {"Dupe000001": text, "Dupe000002": text}
    counts = run(world)
    assert counts["published"] == 1 and counts["dropped"] == 1
    assert ledger(db, "Dupe000002")["reason_code"] == "DUPLICATE_OF_PUBLISHED"
    assert ledger(db, "Dupe000001")["fingerprint"]


def test_selected_finding_reaches_the_copy_and_the_published_outcome_is_recorded(world, db):
    world.refs = [make_ref("Pub0000001", title="Vol digest")]
    world.selections = {"Vol digest": {"candidates": [{"title": "Tech vol premium", "content": "Spread widened."}]}}
    counts = run(world)
    assert counts["published"] == 1
    assert ("copy_finding", "Tech vol premium") in world.calls
    calls = [c[0] for c in world.calls]
    assert calls.index("pdf_pages") < calls.index("download_images") < calls.index("write_copy")
    out = outcome_row(db, "Pub0000001")
    assert out["outcome"] == "published" and out["posts"] == 1
    saved = ledger(db, "Pub0000001")
    assert saved["status"] == "published" and json.loads(saved["folders_json"])[-1] == "Acme Research"


def test_select_prompt_carries_recent_feed_titles(world, db):
    world.refs = [make_ref("Rcnt000001", title="Vol digest")]
    world.recent = [{"title": "Yesterday's CTA post", "timestamp": "2026-10-25T10:00:00+00:00"}]
    run(world)
    assert "Yesterday's CTA post" in world.prompts[0]


def test_select_outage_parks_without_spending_an_attempt(world, db):
    world.refs = [make_ref("Outg000001", title="Vol digest")]
    world.select_outage = {"Vol digest"}
    with pytest.raises(job.ArticlesFailed):  # health turns red, as for a copy-step outage
        run(world)
    saved = ledger(db, "Outg000001")
    assert saved["status"] == "pending" and saved["attempts"] == 0


def test_filtered_articles_count_against_the_per_cycle_cap(world, db):
    world.refs = [make_ref(f"Capd00000{n}", title=f"Recap {n}", hours_ago=n) for n in range(1, 5)]
    world.selections = {f"Recap {n}": {"candidates": []} for n in range(1, 5)}
    counts = run(world, env={"MARKETDESK_MAX_PER_CYCLE": "2"})
    assert counts["held"] == 2


def held_vote(article_id, vote_id="v1", comment="should be in the feed"):
    return (vote_id, "held", None, "marketdesk:" + article_id, article_id, "up", "[]", comment,
            json.dumps({"title": "Routine recap", "publisher": "Acme Research"}), "2026-10-26T15:00:00+00:00")


def test_should_have_published_vote_requeues_with_the_operator_note(world, db):
    old = make_ref("Vote000001", title="Routine recap", hours_ago=72)  # outside the lookback and off the listing
    world.refs = [make_ref("Vote000001", title="Routine recap", hours_ago=1)]
    world.selections = {"Routine recap": {"candidates": []}}
    run(world)
    assert ledger(db, "Vote000001")["status"] == "held"
    world.refs = []
    world.feedback = [held_vote("Vote000001")]
    world.selections = {"Routine recap": {"candidates": [{"title": "It matters", "content": "Measured."}]}}
    world.prompts.clear()
    counts = run(world)
    assert counts["requeued"] == 1 and counts["published"] == 1
    assert "OPERATOR NOTE" in world.prompts[0] and "should be in the feed" in world.prompts[0]
    assert ledger(db, "Vote000001")["status"] == "published" and ledger(db, "Vote000001")["note"] is None
    assert store.feedback_handled() == {"v1"}
    world.prompts.clear()
    assert run(world)["requeued"] == 0 and world.prompts == []  # handled once
    assert old.article_id == "Vote000001"


def test_feedback_sync_refreshes_operator_examples_for_select(world, db, tmp_path):
    world.root = tmp_path / "root"
    world.root.mkdir()
    world.feedback = [("v9", "post", "research-" + "a" * 32, None, "", "down", '["not_relevant"]', "too routine",
                       json.dumps({"title": "Acme recap", "publisher": "Acme Research", "series": "vol digest"}),
                       "2026-10-26T15:00:00+00:00")]
    world.refs = [make_ref("Lern000001", title="Vol digest")]
    run(world)
    assert "OPERATOR PREFERENCES" in world.prompts[0] and "too routine" in world.prompts[0]


def test_feedback_fetch_failure_never_fails_the_cycle(world, db):
    def boom():
        raise RuntimeError("turso down")

    world.refs = [make_ref("Fbck000001", title="Vol digest")]
    deps = build_deps(world)
    deps.feedback_rows = boom
    counts = job.run_cycle(env=dict(ENV), deps=deps)
    assert counts["published"] == 1


def test_spent_preview_quota_defers_without_failing_the_cycle(world, db):
    """marketdesk.ai's PDF preview allowance ran out (2026-10-27): wait for the reset, never fail the unit."""
    from research.marketdesk_scrape import ScrapeError
    world.refs = [make_ref("Qqqqqqqq01", hours_ago=1), make_ref("Qqqqqqqq02", hours_ago=2)]
    world.fetch_errors = {"Qqqqqqqq01": ScrapeError("preview_quota")}
    for _ in range(3):
        counts = run(world)
        assert counts["quota_deferred"] == 2 and counts["parked"] == 0 and counts["failed"] == 0
    saved = ledger(db, "Qqqqqqqq01")
    assert saved["status"] == "pending" and saved["attempts"] == 0 and saved["last_error"] == "preview_quota"
    assert ledger(db, "Qqqqqqqq02") is None  # nothing else claimed while the allowance is spent
    assert ("marketdesk-research", "ok", None) in world.health
    world.fetch_errors = {}
    assert run(world)["published"] == 2
