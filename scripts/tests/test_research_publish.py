"""Private publication transaction, evidence integrity and novelty regressions."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys

import pytest
from research_editor_fixtures import certify
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import db_http
from research import assets, humanizer, publish
from knowledge.sources import newsfeed


@pytest.fixture
def db(monkeypatch):
    connection = sqlite3.connect(":memory:", isolation_level=None)
    root = Path(__file__).resolve().parents[1] / "db/migrations"
    connection.executescript((root / "0001_init.sql").read_text())
    connection.executescript((root / "0071_research_post_sources.sql").read_text())
    connection.executescript((root / "0073_newsfeed_image_sources.sql").read_text())
    monkeypatch.setattr(db_http, "read_env", lambda: ("libsql://test.example", "test-token"))
    def transport(request, timeout):
        assert 0 < timeout <= 10
        payload = json.loads(request.data)
        assert payload["requests"][-1] == {"type": "close"}
        results, errors = [], []
        def condition(cond):
            if cond["type"] == "ok":
                return results[cond["step"]] is not None
            if cond["type"] == "not":
                return not condition(cond["cond"])
            raise AssertionError(cond)
        for step in payload["requests"][0]["batch"]["steps"]:
            if "condition" in step and not condition(step["condition"]):
                results.append(None); errors.append(None); continue
            stmt = step["stmt"]
            args = [int(a["value"]) if a["type"] == "integer" else a.get("value") for a in stmt["args"]]
            try:
                connection.execute(stmt["sql"], args)
                results.append({"affected_row_count": 1}); errors.append(None)
            except sqlite3.Error as exc:
                results.append(None); errors.append({"message": str(exc)})
        return io.BytesIO(json.dumps({"results": [{"type": "ok", "response": {"type": "batch", "result": {"step_results": results, "step_errors": errors}}}]}).encode())
    monkeypatch.setattr(db_http.urllib.request, "urlopen", transport)
    yield connection
    connection.close()


@pytest.fixture
def post(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_DIR", str(tmp_path / "private"))
    pdf = tmp_path / "report.pdf"; pdf.write_bytes(b"%PDF-1.7 test document")
    png = tmp_path / "chart.png"; png.write_bytes(b"\x89PNG\r\n\x1a\n test chart")
    image_url = publish.store_asset(png)
    return certify({"id": publish.stable_post_id("id:abc", "dispersion"), "title": "Dispersion shifted", "content": "New sector evidence.",
            "timestamp": "2026-09-07T12:00:00Z", "images": [image_url], "tags": ["POSITIONING", "VOLATILITY"],
            "source": {"kind": "dropbox", "publisher": "MS", "url": publish.store_asset(pdf), "fileId": "id:abc", "revision": "rev1",
                       "contentHash": "a" * 64, "documentDate": "2026-09-05", "folderDate": "2026-09-06", "pages": [9],
                       "figures": [{"url": image_url, "page": 9, "caption": "Original capture ratios"}]}})


def test_retry_and_revision_update_one_post_and_source(db, post):
    publish.publish(post); publish.publish(post)
    post["title"] = "Corrected dispersion"; post["source"]["revision"] = "rev2"
    post = certify(post)
    publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 1
    assert db.execute("SELECT title FROM posts").fetchone()[0] == "Corrected dispersion"
    assert json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])["revision"] == "rev2"
    assert db.execute("SELECT count(*) FROM research_post_sources").fetchone()[0] == 1


def test_source_failure_rolls_back_post(db, post):
    db.execute("CREATE TRIGGER reject_source BEFORE INSERT ON research_post_sources BEGIN SELECT RAISE(ABORT, 'reject'); END")
    with pytest.raises(db_http.DbHttpError): publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0
    assert not db.in_transaction


def test_revision_failure_preserves_both_prior_rows(db, post):
    publish.publish(post)
    db.execute("CREATE TRIGGER reject_update BEFORE UPDATE ON research_post_sources BEGIN SELECT RAISE(ABORT, 'reject'); END")
    post["title"] = "Must not persist"
    post = certify(post)
    with pytest.raises(db_http.DbHttpError): publish.publish(post)
    assert db.execute("SELECT title FROM posts").fetchone()[0] == "Dispersion shifted"


@pytest.mark.parametrize("change", [
    {"id": "legacy-id"}, {"title": ""}, {"content": ""}, {"timestamp": "2026-09-07T12:00:00"},
    {"images": ["https://media.radon.run/private.png"]}, {"tags": ["lowercase"]},
])
def test_invalid_post_never_reaches_db(db, post, change):
    post.update(change)
    with pytest.raises(ValueError): publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


@pytest.mark.parametrize("key,value", [("kind", "unknown"), ("pages", [0]), ("pages", [True]), ("publisher", ""),
    ("url", "https://dropbox.com/report.pdf"), ("contentHash", "bad"), ("documentDate", "unknown"), ("figures", [])])
def test_invalid_provenance(db, post, key, value):
    post["source"][key] = value
    with pytest.raises(ValueError): publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


def test_text_only_finding_is_publishable(db, post):
    post["images"] = []; post["source"]["figures"] = []
    post = certify(post)
    publish.publish(post)
    assert db.execute("SELECT images FROM posts").fetchone()[0] == "[]"


def test_knowledge_preserves_document_provenance(db, post):
    publish.publish(post)
    doc = list(newsfeed.fetch(db))[0]
    assert doc.metadata["url"] == post["source"]["url"]
    assert doc.metadata["source"]["pages"] == [9]
    assert doc.metadata["source"]["publisher"] == "MS"


def test_knowledge_works_before_migration(db):
    db.execute("DROP TABLE research_post_sources")
    assert list(newsfeed.fetch(db)) == []


def test_novelty_paginates(monkeypatch):
    calls = []
    def execute(sql, args):
        calls.append(args)
        return [("one", "title", "body", "2026-09-07", '["MACRO"]')] if not args[1] else []
    monkeypatch.setattr(publish, "hrana_execute", execute)
    assert publish.recent_posts()[0]["tags"] == ["MACRO"]
    assert calls[1][1] == "one"


def test_novelty_never_silently_truncates(monkeypatch):
    monkeypatch.setattr(publish, "hrana_execute", lambda *a: [("one", "title", "body", "2026-09-07", "[]")])
    with pytest.raises(RuntimeError, match="pagination"): publish.recent_posts()


def test_asset_private_idempotent_and_hash_verified(post):
    name = post["images"][0].rsplit("/", 1)[1]
    path = assets.assets_dir() / name
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert assets.store_asset(path) == post["images"][0]
    path.write_bytes(b"\x89PNG\r\n\x1a\n altered")
    with pytest.raises(ValueError, match="digest"): assets.read_asset(name)


@pytest.mark.parametrize("name", ["../secrets", "../" + "a" * 64 + ".png", "A" * 64 + ".png", "a" * 64 + ".svg", "a.png"])
def test_asset_rejects_traversal_and_unsupported_names(name):
    with pytest.raises(ValueError): assets.read_asset(name)


def test_asset_rejects_symlink(post, tmp_path):
    name = post["images"][0].rsplit("/", 1)[1]
    path = assets.assets_dir() / name
    outside = tmp_path / "outside.png"; outside.write_bytes(path.read_bytes())
    path.unlink(); path.symlink_to(outside)
    with pytest.raises(OSError): assets.read_asset(name)
    with pytest.raises(OSError): assets.store_asset(path)


def test_asset_rejects_directory_symlink(post, tmp_path, monkeypatch):
    original = assets.assets_dir()
    alternate = tmp_path / "alternate"; alternate.mkdir(); (alternate / "assets").symlink_to(original)
    monkeypatch.setenv("RADON_RESEARCH_DIR", str(alternate))
    with pytest.raises(OSError): assets.read_asset(post["images"][0].rsplit("/", 1)[1])


def test_asset_rejects_extension_mismatch_and_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_DIR", str(tmp_path / "private"))
    wrong = tmp_path / "bad.png"; wrong.write_bytes(b"<script>bad</script>")
    with pytest.raises(ValueError, match="format"): assets.store_asset(wrong)
    monkeypatch.setitem(assets.MAX_BYTES, "png", 8)
    wrong.write_bytes(b"\x89PNG\r\n\x1a\nextra")
    with pytest.raises(ValueError, match="size"): assets.store_asset(wrong)


def test_demo_mirror_excludes_private_research_at_query_boundary(db, post):
    publish.publish(post)
    db.execute("INSERT INTO posts(id,title,timestamp,created_at,updated_at) VALUES ('public-post','Public','2026-09-07','now','now')")
    source = (Path(__file__).resolve().parents[1] / "db/mirror_newsfeed_to_demo.js").read_text()
    import re
    query = re.search(r"sql: `(SELECT id, title, content, timestamp, images, raw_images, image_sources, tags, tags_text, tags_vision, created_at, updated_at\s+FROM posts.*?)`", source, re.S).group(1)
    assert [row[0] for row in db.execute(query, (400,)).fetchall()] == ["public-post"]


def test_transaction_rejects_missing_commit_even_without_reported_error(db, monkeypatch):
    response = {"results": [{"type": "ok", "response": {"type": "batch", "result": {"step_results": [{}, {}, None], "step_errors": []}}}]}
    monkeypatch.setattr(db_http.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(response).encode()))
    with pytest.raises(db_http.DbHttpError, match="commit"): db_http.hrana_transaction([("SELECT 1", ())])


def test_transaction_missing_credentials(monkeypatch):
    monkeypatch.setattr(db_http, "read_env", lambda: ("", ""))
    with pytest.raises(db_http.DbHttpError): db_http.hrana_transaction([("SELECT 1", ())])


def test_transaction_transport_error_redacts_details(db, monkeypatch):
    def broken(*a, **k): raise TimeoutError("secret-token")
    monkeypatch.setattr(db_http.urllib.request, "urlopen", broken)
    with pytest.raises(db_http.DbHttpError) as error: db_http.hrana_transaction([("SELECT 1", ())])
    assert "secret-token" not in str(error.value)


def test_id_stable_across_retries_but_distinct_for_findings():
    assert publish.stable_post_id("id:a", "finding") == publish.stable_post_id("id:a", "finding")
    assert publish.stable_post_id("id:a", "finding") != publish.stable_post_id("id:a", "other")
    assert publish.stable_post_id("id:a", "finding") != publish.stable_post_id("id:b", "finding")


@pytest.mark.parametrize("days", [0, 91, True, "90"])
def test_invalid_novelty_window(days):
    with pytest.raises(ValueError): publish.recent_posts(days)


def test_required_stable_id_components():
    with pytest.raises(ValueError): publish.stable_post_id("", "finding")
    with pytest.raises(ValueError): publish.stable_post_id("id:a", "")


def test_em_dash_copy_cannot_publish_with_bank_publisher_or_forged_clean_digest(db, post):
    post["content"] = "Rates \u2014 positioning"
    post["source"]["publisher"] = "Goldman Sachs"
    post['source']['humanizer']['copy_sha256'] = humanizer.copy_digest(post)
    with pytest.raises(publish.HumanizerCertificateError, match='rubric'):
        publish.publish(post)
    assert db.execute('SELECT count(*) FROM posts').fetchone()[0] == 0


def test_empty_caption_publishes(db, post):
    post["source"]["figures"][0]["caption"] = ""
    post = certify(post)
    publish.publish(post)
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert source["figures"][0]["caption"] == ""


def test_long_title_and_body_publish(db, post):
    post["title"] = "Macro " + "T" * 600
    post["content"] = "B" * 40000
    post = certify(post)
    publish.publish(post)
    assert db.execute("SELECT title, content FROM posts").fetchone() == (post["title"], post["content"])


def test_publication_preserves_evidence_quotes_assets_and_financial_punctuation(db, post):
    post["title"] = "Macro demand"
    post["content"] = "Demand fell -5.65%. The 2-3% range isn't a forecast."
    post["source"]["source_quote"] = "Source wording\u2014unchanged."
    post["source"]["date_evidence"] = {"source_quote": "Report\u2014September 5, 2026"}
    post = certify(post)
    before = copy.deepcopy(post)
    names = [post["source"]["url"].rsplit("/", 1)[1], post["images"][0].rsplit("/", 1)[1]]
    original_bytes = [(assets.assets_dir() / name).read_bytes() for name in names]
    publish.publish(post)
    assert post == before
    assert db.execute("SELECT content FROM posts").fetchone()[0] == before["content"]
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert source == {k: v for k, v in before["source"].items() if k != "humanizer"}
    assert [(assets.assets_dir() / name).read_bytes() for name in names] == original_bytes


def test_invalid_figure_page_or_asset_extension(db, post):
    invalid = copy.deepcopy(post)
    invalid["source"]["figures"][0]["page"] = 10
    with pytest.raises(ValueError, match="cited"): publish.publish(invalid)
    invalid = copy.deepcopy(post)
    invalid["source"]["url"] = post["images"][0]
    with pytest.raises(ValueError, match="URL"): publish.publish(invalid)
    invalid = copy.deepcopy(post)
    invalid["images"] *= 13
    with pytest.raises(ValueError, match="images"): publish.publish(invalid)


def test_store_rejects_unsupported_extension_and_symlink_root(tmp_path, monkeypatch):
    unsupported = tmp_path / "bad.svg"; unsupported.write_bytes(b"<svg/>")
    with pytest.raises(ValueError, match="PNG, PDF and JSON"): assets.store_asset(unsupported)
    png = tmp_path / "chart.png"; png.write_bytes(b"\x89PNG\r\n\x1a\n chart")
    real = tmp_path / "real"; real.mkdir()
    (tmp_path / "assets").symlink_to(real)
    monkeypatch.setenv("RADON_RESEARCH_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="directory"): assets.store_asset(png)


def test_original_bank_attribution_is_accepted(db,post):
    post['source']['publisher']='Goldman Sachs'
    assert publish.publish(post)==post['id']


def test_publication_manifest_binds_exact_source_pdf(db, tmp_path, monkeypatch):
    from research.manifest import build_manifest
    # Reuse the real asset/publish fixture construction without external data.
    monkeypatch.setenv('RADON_RESEARCH_DIR', str(tmp_path/'research'))
    pdf = tmp_path/'source.pdf';pdf.write_bytes(b'%PDF-1.4 fixture')
    url = publish.store_asset(pdf)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    (tmp_path/'page-0001.md').write_text('Report date: 7 September 2026. Capex 20.')
    manifest = build_manifest({'source_sha256':digest,'pages':[{'page_number':1,'markdown_file':'page-0001.md','needs_ocr':False}]},tmp_path)
    file = tmp_path/'manifest.json';file.write_text(json.dumps(manifest))
    evidence_url = publish.store_asset(file)
    post = {'id':'research-'+'d'*32,'title':'Sector capex research','content':'Capex 20.',
        'timestamp':'2026-09-07T12:00:00+00:00','images':[],'tags':['CAPEX'],
        'source':{'kind':'dropbox','publisher':'Research','url':url,'evidenceUrl':evidence_url,
            'fileId':'id:test','revision':'r1','contentHash':'a'*64,'documentDate':'2026-09-07',
            'folderDate':'2026-09-07','pages':[1],'figures':[]}}
    post = certify(post)
    publish.publish(post)
    saved = json.loads(db.execute('SELECT provenance_json FROM research_post_sources').fetchone()[0])
    assert saved['evidenceUrl'] == evidence_url
    other = tmp_path/'other.pdf';other.write_bytes(b'%PDF-1.4 different')
    post['source']['url'] = publish.store_asset(other)
    with pytest.raises(ValueError, match='original source PDF'): publish.publish(post)


@pytest.mark.parametrize("tamper", ["missing", "changed_copy", "inflated_score", "qualitative"])
def test_final_copy_requires_honest_current_certificate(db, post, tamper):
    if tamper == "missing":
        post["source"].pop("humanizer")
    elif tamper == "changed_copy":
        post["content"] += " Changed after scoring."
    elif tamper == "inflated_score":
        post["source"]["humanizer"]["score"] = 101
    else:
        post["source"]["humanizer"]["qualitative"] = [{"field": "title", "start": 0, "end": 10, "category": "filler"}]
    with pytest.raises(publish.HumanizerCertificateError):
        publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


def test_scoring_audit_is_not_exposed_in_feed_provenance(db, post):
    publish.publish(post)
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert "humanizer" not in source
    assert "humanizer" in post["source"], "publication must not mutate durable outbox payload"


@pytest.fixture
def marketdesk_post(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_DIR", str(tmp_path / "private"))
    pdf = tmp_path / "marketdesk.pdf"; pdf.write_bytes(b"%PDF-1.7 marketdesk article")
    pngs = []
    for index in range(14):
        png = tmp_path / f"img{index}.png"; png.write_bytes(b"\x89PNG\r\n\x1a\n image %d" % index)
        pngs.append(publish.store_asset(png))
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return {"id": publish.stable_post_id("marketdesk:4AJs5Adl2Yp", "article"), "title": "Curve steepens",
            "content": "Two cuts priced.", "timestamp": "2026-10-26T13:00:00+00:00", "images": pngs[:2],
            "tags": ["RATES", "MARKETDESK"],
            "source": {"kind": "marketdesk", "publisher": "MarketDesk", "articleId": "4AJs5Adl2Yp",
                       "articleUrl": "https://marketdesk.ai/library/browse/4AJs5Adl2Yp", "contentHash": sha,
                       "documentDate": "2026-10-26", "folderDate": "2026-10-26", "pages": [1],
                       "url": publish.store_asset(pdf),
                       "figures": [{"url": pngs[0], "caption": "2s10s spread"}, {"url": pngs[1], "caption": ""}],
                       "summary": ["Fed priced for two cuts", "Curve steepened 12bp"],
                       "extraImages": pngs[2:]},
            "_pngs": pngs}


def _clean(post):
    post = copy.deepcopy(post); post.pop("_pngs", None)
    return certify(post)


def test_marketdesk_post_without_humanizer_certificate_refused(db, marketdesk_post):
    post = _clean(marketdesk_post)
    post["source"].pop("humanizer")
    with pytest.raises(ValueError, match="Humanizer certificate"): publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


def test_marketdesk_post_publishes_with_provenance(db, marketdesk_post):
    post = _clean(marketdesk_post)
    assert publish.publish(post) == post["id"]
    saved = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert saved == {k: v for k, v in post["source"].items() if k != "humanizer"}
    assert json.loads(db.execute("SELECT images FROM posts").fetchone()[0]) == post["images"]


def test_marketdesk_minimal_without_figures_summary_or_extras(db, marketdesk_post):
    post = _clean(marketdesk_post)
    post["images"] = []
    post["source"].update(figures=[], summary=[], extraImages=[])
    post = certify(post)
    publish.publish(post)
    del post["source"]["summary"], post["source"]["extraImages"]
    publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 1


@pytest.mark.parametrize("mutate", [
    lambda s, p: s.update(articleId="bad-id!"),
    lambda s, p: s.update(articleId="short"),
    lambda s, p: s.update(articleId="A" * 17, articleUrl="https://marketdesk.ai/library/browse/" + "A" * 17),
    lambda s, p: s.update(articleUrl="https://marketdesk.ai/library/browse/OTHER12345"),
    lambda s, p: s.update(articleUrl="http://marketdesk.ai/library/browse/4AJs5Adl2Yp"),
    lambda s, p: s.update(contentHash="a" * 64),
    lambda s, p: s.update(contentHash="bad"),
    lambda s, p: s.update(publisher=""),
    lambda s, p: s.update(documentDate="2026/10/26"),
    lambda s, p: s.update(folderDate=""),
    lambda s, p: s.update(pages=[2]),
    lambda s, p: s.update(pages=[True]),
    lambda s, p: s.update(pages=[1, 2]),
    lambda s, p: s.update(url=p["images"][0]),
    lambda s, p: s.update(url="https://marketdesk.ai/standalone/abc.pdf"),
    lambda s, p: s["figures"][0].update(url=s["url"]),
    lambda s, p: s["figures"][0].update(caption=None),
    lambda s, p: s["figures"].append("not a figure"),
    lambda s, p: s.update(summary="not a list"),
    lambda s, p: s.update(summary=["x" * 501]),
    lambda s, p: s.update(summary=["ok"] * 21),
    lambda s, p: s.update(summary=[3]),
    lambda s, p: s.update(extraImages=[s["url"]]),
    lambda s, p: s.update(extraImages=["https://media.radon.run/x.png"]),
    lambda s, p: s.update(extraImages=s["extraImages"] * 4),
    lambda s, p: s.update(kind="MarketDesk"),
])
def test_invalid_marketdesk_provenance(db, marketdesk_post, mutate):
    post = _clean(marketdesk_post)
    mutate(post["source"], post)
    with pytest.raises(ValueError): publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


def test_marketdesk_images_must_equal_figures(db, marketdesk_post):
    post = _clean(marketdesk_post)
    post["images"] = list(reversed(post["images"]))
    with pytest.raises(ValueError, match="figures"): publish.publish(post)
    post = _clean(marketdesk_post)
    post["images"] = post["images"][:1]
    with pytest.raises(ValueError): publish.publish(post)


def test_marketdesk_rejects_more_than_twelve_figures(db, marketdesk_post):
    pngs = marketdesk_post["_pngs"]
    post = _clean(marketdesk_post)
    post["source"]["figures"] = [{"url": u, "caption": ""} for u in pngs[:13]]
    post["images"] = pngs[:13]
    with pytest.raises(ValueError): publish.publish(post)
    post["source"]["figures"] = post["source"]["figures"][:12]
    post["images"] = pngs[:12]
    post = certify(post)
    publish.publish(post)


def test_marketdesk_rejects_tampered_asset(db, marketdesk_post):
    post = _clean(marketdesk_post)
    name = post["source"]["extraImages"][0].rsplit("/", 1)[1]
    (assets.assets_dir() / name).write_bytes(b"\x89PNG\r\n\x1a\n tampered")
    with pytest.raises(ValueError): publish.publish(post)


def test_dropbox_fields_do_not_satisfy_marketdesk(db, post):
    post["source"]["kind"] = "marketdesk"
    with pytest.raises(ValueError): publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


def test_dropbox_figure_without_page_still_rejected(db, post):
    del post["source"]["figures"][0]["page"]
    with pytest.raises(ValueError, match="cited"): publish.publish(post)


def test_marketdesk_article_evidence_publishes_without_fabricated_pdf(db, marketdesk_post):
    post = _clean(marketdesk_post)
    source = post["source"]
    source.update(evidenceKind="article", url=source["articleUrl"], pages=[])
    source.pop("summary", None)
    assert publish.publish(post) == post["id"]
    saved = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert saved["evidenceKind"] == "article" and "summary" not in saved


@pytest.mark.parametrize("change", [
    {"url": "https://evil.example/report"}, {"pages": [1]}, {"summary": ["raw source bullet"]},
    {"pdfUrl": "/api/newsfeed/research/files/" + "a" * 64 + ".pdf"},
    {"pdfHash": "a" * 64},
    {"pdfUrl": "/api/newsfeed/research/files/" + "a" * 64 + ".pdf", "pdfHash": "b" * 64},
])
def test_marketdesk_article_rejects_unbound_source_or_download(db, marketdesk_post, change):
    post = _clean(marketdesk_post)
    source = post["source"]
    source.update(evidenceKind="article", url=source["articleUrl"], pages=[])
    source.pop("summary", None)
    source.update(change)
    with pytest.raises(ValueError):
        publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


@pytest.mark.parametrize('title,body,current,reason', [
    ('TransUnion (TRU) earnings', 'TransUnion revenue rose 10%.', {'HOOD'}, 'EQUITY_RESEARCH_NOT_IN_BOOK'),
    ('Robinhood (HOOD) earnings', 'Robinhood EPS rose 10%.', set(), 'EQUITY_RESEARCH_NOT_IN_BOOK'),
    ('Robinhood (HOOD) earnings', 'Robinhood EPS rose 10%.', None, 'EQUITY_RESEARCH_BOOK_UNAVAILABLE'),
])
def test_publication_rechecks_current_book_even_with_valid_editorial_certificate(db,post,monkeypatch,title,body,current,reason):
    from research import book
    monkeypatch.setattr(book,'current_tickers',lambda:current)
    post=certify({**post,'title':title,'content':body})
    with pytest.raises(ValueError,match=reason) as caught:
        publish.publish(post)
    assert caught.value.reason_code==reason
    assert db.execute('SELECT count(*) FROM posts').fetchone()[0]==0


def test_legacy_certified_outbox_cannot_publish_after_book_removal(db,post,monkeypatch,tmp_path):
    from research import book
    from research.state import State
    from research.worker import flush_outbox
    post=certify({**post,'title':'Robinhood (HOOD) earnings','content':'Robinhood EPS rose 10%.'})
    current={'HOOD'}
    monkeypatch.setattr(book,'current_tickers',lambda:current)
    humanizer.validate_certificate(post)
    state=State(tmp_path/'outbox.sqlite')
    with state.db:
        state.db.execute("INSERT INTO work(key,file_id,rev,path,scope,metadata,status,result) VALUES('work','id:abc','r1','/report.pdf','scope','{}','complete',?)",
                         (json.dumps({'status':'reviewed','items':1}),))
        state.db.execute("INSERT INTO outbox VALUES(?,'work',?,'pending')",(post['id'],json.dumps(post)))
    current.clear()
    assert flush_outbox(state,publish)==0
    assert flush_outbox(state,publish)==0
    assert state.outbox()==[]
    row=state.db.execute('SELECT status,error,result FROM work').fetchone()
    assert row['status']=='complete' and row['error']=='EQUITY_RESEARCH_NOT_IN_BOOK'
    assert json.loads(row['result'])['status']=='dropped'
    assert json.loads(state.db.execute('SELECT payload FROM outbox').fetchone()[0])==post
    assert db.execute('SELECT count(*) FROM posts').fetchone()[0]==0
    state.close()


@pytest.mark.parametrize('caption,current,blocked', [
    ('Meta (META): Buy, price target $850.', {'HOOD'}, True),
    ('Robinhood (HOOD): Buy, price target $150.', {'HOOD'}, False),
    ('US equity fund flows by sector.', {'HOOD'}, False),
])
def test_rendered_caption_is_subject_to_fresh_publication_admission(db, post, monkeypatch, caption, current, blocked):
    from research import book
    monkeypatch.setattr(book, 'current_tickers', lambda: current)
    post['source']['figures'][0]['caption'] = caption
    post = certify(post)
    humanizer.validate_certificate(post)
    if blocked:
        with pytest.raises(publish.PublicationAdmissionError, match='EQUITY_RESEARCH_NOT_IN_BOOK'):
            publish.publish(post)
        assert db.execute('SELECT count(*) FROM posts').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM research_post_sources').fetchone()[0] == 0
    else:
        publish.publish(post)
        assert db.execute('SELECT count(*) FROM posts').fetchone()[0] == 1
