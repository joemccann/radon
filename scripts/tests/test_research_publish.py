"""Private publication transaction, evidence integrity and novelty regressions."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import db_http
from research import assets, publish
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
    return {"id": publish.stable_post_id("id:abc", "dispersion"), "title": "Dispersion shifted", "content": "New sector evidence.",
            "timestamp": "2026-09-07T12:00:00Z", "images": [image_url], "tags": ["POSITIONING", "VOLATILITY"],
            "source": {"kind": "dropbox", "publisher": "MS", "url": publish.store_asset(pdf), "fileId": "id:abc", "revision": "rev1",
                       "contentHash": "a" * 64, "documentDate": "2026-09-05", "folderDate": "2026-09-06", "pages": [9],
                       "figures": [{"url": image_url, "page": 9, "caption": "Original capture ratios"}]}}


def test_retry_and_revision_update_one_post_and_source(db, post):
    publish.publish(post); publish.publish(post)
    post["title"] = "Corrected dispersion"; post["source"]["revision"] = "rev2"
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


@pytest.mark.parametrize("field", ["title", "content", "publisher", "caption", "tag"])
@pytest.mark.parametrize("dash", ["\u2014", "&mdash;", "&#8212;", "&#x2014;"])
def test_authored_em_dash_never_reaches_publication(db, post, field, dash):
    text = f"Rates {dash} positioning"
    if field in ("title", "content"):
        post[field] = text
    elif field == "publisher":
        post["source"][field] = text
    elif field == "caption":
        post["source"]["figures"][0][field] = text
    else:
        post["tags"] = [text]
    with pytest.raises(ValueError):
        publish.publish(post)
    assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


@pytest.mark.parametrize("dash", ["\u2014", "&mdash;", "&#8212;", "&#x2014;"])
def test_rendered_copy_gate_checks_tags_before_caller_normalization(dash):
    with pytest.raises(ValueError, match="em dash"):
        publish.validate_rendered_copy("Rates", "New evidence.", "JPMorgan", [], [f"FLOW{dash}RATES"])


def test_publication_preserves_evidence_quotes_assets_and_financial_punctuation(db, post):
    post["content"] = "Demand fell -5.65%. The 2-3% range isn't a forecast."
    post["source"]["source_quote"] = "Source wording\u2014unchanged."
    post["source"]["date_evidence"] = {"source_quote": "Report\u2014September 5, 2026"}
    before = copy.deepcopy(post)
    names = [post["source"]["url"].rsplit("/", 1)[1], post["images"][0].rsplit("/", 1)[1]]
    original_bytes = [(assets.assets_dir() / name).read_bytes() for name in names]
    publish.publish(post)
    assert post == before
    assert db.execute("SELECT content FROM posts").fetchone()[0] == before["content"]
    source = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert source == before["source"]
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


@pytest.mark.parametrize('field',['title','content','publisher','caption','tags'])
@pytest.mark.parametrize('name',['ZeroHedge','ZEROHEDGE','ZERO HEDGE','zero\u00a0hedge','Zero-Hedge'])
def test_intermediary_never_enters_rendered_research_copy(db,post,field,name):
    if field=='publisher':post['source']['publisher']=name
    elif field=='caption':post['source']['figures'][0]['caption']=name
    elif field=='tags':post['tags']=[name]
    else:post[field]=name
    with pytest.raises(ValueError):publish.publish(post)
    assert db.execute('SELECT count(*) FROM posts').fetchone()[0]==0


def test_original_bank_attribution_is_accepted(db,post):
    post['source']['publisher']='Goldman Sachs'
    assert publish.publish(post)==post['id']


@pytest.mark.parametrize('control',['\u200b','\u00ad','\u200d'])
def test_invisible_format_controls_do_not_bypass_attribution_guard(control):
    with pytest.raises(ValueError):
        publish.validate_rendered_copy('Zero'+control+'Hedge','Research','Goldman Sachs',[],['MACRO'])


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
    post = {'id':'research-'+'d'*32,'title':'Capex research','content':'Capex 20.',
        'timestamp':'2026-09-07T12:00:00+00:00','images':[],'tags':['CAPEX'],
        'source':{'kind':'dropbox','publisher':'Research','url':url,'evidenceUrl':evidence_url,
            'fileId':'id:test','revision':'r1','contentHash':'a'*64,'documentDate':'2026-09-07',
            'folderDate':'2026-09-07','pages':[1],'figures':[]}}
    publish.publish(post)
    saved = json.loads(db.execute('SELECT provenance_json FROM research_post_sources').fetchone()[0])
    assert saved['evidenceUrl'] == evidence_url
    other = tmp_path/'other.pdf';other.write_bytes(b'%PDF-1.4 different')
    post['source']['url'] = publish.store_asset(other)
    with pytest.raises(ValueError, match='original source PDF'): publish.publish(post)
