"""MarketDesk JSON API client: latest listing, article extra/path, PDF blob, public chart images.

Parsers run on REAL captures of the signed-in API (fixtures/marketdesk/api_*.json, 2026-10-26). No
network: the session is a fake and public image GETs go through a fake opener plus a monkeypatched
resolver.
"""
import io
import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from research import marketdesk_scrape as scrape

FIXTURES = Path(__file__).parent / "fixtures" / "marketdesk"
PDF_BYTES = (FIXTURES / "sample.pdf").read_bytes()
JPEG_BYTES = (FIXTURES / "chart.jpg").read_bytes()
WEBP_BYTES = (FIXTURES / "chart.webp").read_bytes()
SVG_BYTES = (FIXTURES / "logo.svg").read_bytes()
CHARTS = "https://box1.nyc3.cdn.digitaloceanspaces.com/charts/"


def load(name):
    return json.loads((FIXTURES / f"api_{name}.json").read_text())


def _png(size=(8, 8), color=(1, 2, 3)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


class FakeSession:
    """Answers api()/blob() from the captured payloads; records every call."""

    def __init__(self, api=None, blobs=None):
        self.routes = {
            ("POST", "/api/mobile/latest/latest"): load("latest"),
            ("POST", "/api/mobile/items/provision"): load("provision"),
            ("GET", "/api/mobile/items/4AJs5Adl2Yp/extra"): load("extra_4AJs5Adl2Yp"),
            ("GET", "/api/mobile/items/4AJs5Adl2Yp/path"): load("path_4AJs5Adl2Yp"),
            ("GET", "/api/mobile/items/Pd4eYWnz5FH/extra"): load("extra_Pd4eYWnz5FH"),
            ("GET", "/api/mobile/items/Pd4eYWnz5FH/path"): load("path_4AJs5Adl2Yp"),
            ("GET", "/api/mobile/items/eQJttFDd58v/extra"): load("extra_4AJs5Adl2Yp"),
            ("GET", "/api/mobile/items/eQJttFDd58v/path"): load("path_eQJttFDd58v"),
            **(api or {}),
        }
        self.blobs = blobs or {}
        self.calls = []

    def api(self, method, path, body=None):
        self.calls.append((method, path, body))
        value = self.routes[(method, path)]
        if isinstance(value, Exception):
            raise value
        return value

    def blob(self, path, *, limit):
        self.calls.append(("BLOB", path, limit))
        value = self.blobs[path]
        if isinstance(value, Exception):
            raise value
        return value


class FakeResponse(io.BytesIO):
    def __init__(self, status, body, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append({"url": request.full_url, "headers": dict(request.header_items())})
        status, body = self.routes[request.full_url]
        if status >= 300:
            raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(b""))
        return FakeResponse(status, body)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Every host resolves public unless a test overrides it."""
    table = {}
    monkeypatch.setattr(scrape, "_resolve_host", lambda host: table.get(host, ["93.184.216.34"]))
    monkeypatch.delenv("MARKETDESK_ASSET_HOSTS", raising=False)
    return table


def opener(monkeypatch, routes):
    fake = FakeOpener(routes)
    monkeypatch.setattr(scrape, "_PUBLIC_OPENER", fake)
    return fake


def ref(article_id="4AJs5Adl2Yp", institution="CBOE"):
    return scrape.ArticleRef(article_id, "https://marketdesk.ai/library/browse/" + article_id,
                             datetime(2026, 10, 26, 13, 55, tzinfo=timezone.utc), "Macro Volatility Digest Oct26",
                             institution)


# ---- listing -------------------------------------------------------------------------------

def test_list_latest_calls_latest_then_provision_with_exact_bodies():
    session = FakeSession()
    refs = scrape.list_latest(session)
    assert session.calls[0] == ("POST", "/api/mobile/latest/latest", {"institutions": []})
    assert session.calls[1] == ("POST", "/api/mobile/items/provision", {"pathIds": load("latest")})
    assert [r.article_id for r in refs] == ["Pd4eYWnz5FH", "eQJttFDd58v", "4AJs5Adl2Yp", "haXGoK7Z6VU"]


def test_parse_items_reads_unix_time_title_and_institution():
    refs = {r.article_id: r for r in scrape.parse_items(load("provision"))}
    cboe = refs["4AJs5Adl2Yp"]
    assert cboe.published_at == datetime.fromtimestamp(1793030100, timezone.utc)
    assert cboe.title == "Macro Volatility Digest Oct26"
    assert cboe.institution == "CBOE"
    assert cboe.url == "https://marketdesk.ai/library/browse/4AJs5Adl2Yp"
    # runs of spaces in the library names collapse; the name is otherwise kept
    assert refs["Pd4eYWnz5FH"].title.startswith("UBS S&T US Semiconductors and Semi Equipment SemiBytes")


def test_parse_items_sorts_newest_first_dedups_and_drops_bad_rows():
    rows = [
        {"pathId": "aaaaaaaaaa1", "name": "Old", "t": 100, "type": "application/pdf"},
        {"pathId": "aaaaaaaaaa2", "name": "New", "t": 200, "type": "application/pdf"},
        {"pathId": "aaaaaaaaaa2", "name": "Dup", "t": 300, "type": "application/pdf"},
        {"pathId": "bad id!", "name": "x", "t": 300, "type": "application/pdf"},
        {"pathId": "aaaaaaaaaa3", "name": "Folder", "t": 300, "type": "folder"},
        {"pathId": "aaaaaaaaaa4", "name": "No time", "type": "application/pdf"},
        {"pathId": "aaaaaaaaaa5", "name": "", "t": 300, "type": "application/pdf"},
        "garbage",
    ]
    assert [(r.article_id, r.title) for r in scrape.parse_items(rows)] == [
        ("aaaaaaaaaa5", "aaaaaaaaaa5"), ("aaaaaaaaaa2", "New"), ("aaaaaaaaaa1", "Old")]


def test_list_latest_refuses_a_non_list_payload():
    session = FakeSession({("POST", "/api/mobile/latest/latest"): {"oops": 1}})
    with pytest.raises(scrape.ScrapeError, match="bad_response"):
        scrape.list_latest(session)


def test_list_latest_skips_provision_when_nothing_is_listed():
    session = FakeSession({("POST", "/api/mobile/latest/latest"): []})
    assert scrape.list_latest(session) == []
    assert len(session.calls) == 1


def test_list_latest_drops_invalid_ids_before_provision():
    session = FakeSession({("POST", "/api/mobile/latest/latest"): ["4AJs5Adl2Yp", "../x", 7]})
    scrape.list_latest(session)
    assert session.calls[1][2] == {"pathIds": ["4AJs5Adl2Yp"]}


# ---- article -------------------------------------------------------------------------------

def test_fetch_article_reads_summary_images_folders_and_publisher():
    session = FakeSession()
    article = scrape.fetch_article(session, ref())
    assert ("GET", "/api/mobile/items/4AJs5Adl2Yp/extra", None) in session.calls
    assert ("GET", "/api/mobile/items/4AJs5Adl2Yp/path", None) in session.calls
    assert len(article.summary) == 5
    assert article.summary[0].startswith("Tech Risk Premium Surge: QQQ-SPX 1M vol spread widens to 10%")
    assert all("**" not in item for item in article.summary)
    assert len(article.image_urls) == 9
    assert article.image_urls[0] == CHARTS + "4AJs5Adl2Yp/i-334c01fa33e8ae69a93c7817b993c7418b6449164807014db062a64d6511c7d1.png"
    assert article.folders == ["2026", "October", "Oct 26", "CBOE"]
    assert article.publisher == "CBOE"
    assert article.title == "Macro Volatility Digest Oct26"


def test_fetch_article_publisher_is_the_bank_folder_not_the_desk():
    article = scrape.fetch_article(FakeSession(), ref("eQJttFDd58v", institution="S&T"))
    assert article.folders == ["2026", "October", "Oct 26", "Goldman Sachs", "S&T"]
    assert article.publisher == "Goldman Sachs"


def test_unprocessed_item_has_no_summary_and_no_images():
    article = scrape.fetch_article(FakeSession(), ref("Pd4eYWnz5FH", institution="UBS"))
    assert article.summary == [] and article.image_urls == []


def test_fetch_article_refuses_bad_ref_without_a_call():
    session = FakeSession()
    with pytest.raises(scrape.ScrapeError, match="bad_ref"):
        scrape.fetch_article(session, ref("../../x"))
    assert session.calls == []


@pytest.mark.parametrize("text,expected", [
    ("- **Head**: body text", ["Head: body text"]),
    ("* plain bullet\n\n- **A**: one\n  - nested two", ["plain bullet", "A: one", "nested two"]),
    ("**Document Missing**: No source document provided for extraction", []),
    ("", []),
    (None, []),
    ("- " + "x" * 900, ["x" * 900]),
])
def test_parse_summary(text, expected):
    assert scrape.parse_summary(text) == expected


def test_parse_summary_caps_item_count():
    with pytest.raises(scrape.ScrapeError, match="summary_too_large"):
        scrape.parse_summary("\n".join(f"- item {i}" for i in range(40)))


def test_parse_images_validates_ids_dedups_and_orders_by_page():
    iid = "i-" + "a" * 64 + ".png"
    rows = [
        {"iid": "i-" + "b" * 64 + ".png", "pathId": "4AJs5Adl2Yp", "page": 2},
        {"iid": iid, "pathId": "4AJs5Adl2Yp", "page": 0},
        {"iid": iid, "pathId": "4AJs5Adl2Yp", "page": 0},
        {"iid": "../../etc/passwd", "pathId": "4AJs5Adl2Yp", "page": 0},
        {"iid": "i-" + "c" * 64 + ".png", "pathId": "OTHERITEM01", "page": 0},
        "garbage",
    ]
    assert scrape.parse_images(rows, "4AJs5Adl2Yp") == [
        CHARTS + "4AJs5Adl2Yp/" + iid, CHARTS + "4AJs5Adl2Yp/i-" + "b" * 64 + ".png"]


def test_parse_folders_drops_current_and_bad_rows():
    assert scrape.parse_folders(load("path_4AJs5Adl2Yp")) == ["2026", "October", "Oct 26", "CBOE"]
    assert scrape.parse_folders("nope") == []
    assert scrape.parse_folders([{"pathId": "current", "name": "Current"}, {"name": ""}, 3]) == []


# ---- PDF -----------------------------------------------------------------------------------

def _article(**kw):
    base = dict(ref=ref(), title="t", publisher="CBOE", summary=["a"], image_urls=[], folders=[])
    base.update(kw)
    return scrape.Article(**base)


def test_download_pdf_fetches_the_blob_and_writes_private_file(tmp_path):
    session = FakeSession(blobs={"/files/4AJs5Adl2Yp/blob": (PDF_BYTES, "application/pdf")})
    path = scrape.download_pdf(session, _article(), tmp_path)
    assert path == tmp_path / "4AJs5Adl2Yp.pdf" and path.read_bytes() == PDF_BYTES
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert session.calls == [("BLOB", "/files/4AJs5Adl2Yp/blob", scrape.MAX_PDF_BYTES)]


def test_download_pdf_rejects_wrong_magic(tmp_path):
    session = FakeSession(blobs={"/files/4AJs5Adl2Yp/blob": (b"<html>viewer</html>", "text/html")})
    with pytest.raises(scrape.ScrapeError, match="not_pdf"):
        scrape.download_pdf(session, _article(), tmp_path)


def test_download_pdf_propagates_session_errors(tmp_path):
    session = FakeSession(blobs={"/files/4AJs5Adl2Yp/blob": scrape.ScrapeError("unauthenticated")})
    with pytest.raises(scrape.ScrapeError, match="unauthenticated"):
        scrape.download_pdf(session, _article(), tmp_path)


# ---- images --------------------------------------------------------------------------------

def test_download_images_converts_dedupes_and_writes_png(tmp_path, monkeypatch):
    urls = [CHARTS + "4AJs5Adl2Yp/a.png", CHARTS + "4AJs5Adl2Yp/b.jpg", CHARTS + "4AJs5Adl2Yp/c.png",
            CHARTS + "4AJs5Adl2Yp/d.webp", CHARTS + "4AJs5Adl2Yp/e.svg"]
    fake = opener(monkeypatch, {urls[0]: (200, _png()), urls[1]: (200, JPEG_BYTES), urls[2]: (200, _png()),
                                urls[3]: (200, WEBP_BYTES), urls[4]: (200, SVG_BYTES)})
    paths = scrape.download_images(None, _article(image_urls=urls), tmp_path)
    assert len(paths) == 3  # duplicate PNG and the SVG dropped
    assert all(p.read_bytes().startswith(b"\x89PNG") and oct(p.stat().st_mode & 0o777) == "0o600" for p in paths)
    assert all("Cookie" not in r["headers"] for r in fake.requests)


def test_download_images_host_allowlist(tmp_path, monkeypatch):
    urls = ["https://evil.example/x.png", "http://box1.nyc3.cdn.digitaloceanspaces.com/charts/x.png",
            "https://example-cdn.com/y.png", CHARTS + "ok.png"]
    fake = opener(monkeypatch, {urls[2]: (200, _png((4, 4))), urls[3]: (200, _png())})
    assert len(scrape.download_images(None, _article(image_urls=urls), tmp_path)) == 1
    monkeypatch.setenv("MARKETDESK_ASSET_HOSTS", "example-cdn.com")
    assert len(scrape.download_images(None, _article(image_urls=urls), tmp_path / "2")) == 2
    assert {r["url"] for r in fake.requests} == {urls[2], urls[3]}


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "100.100.1.1",
                                     "192.168.1.1", "::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_download_images_refuses_private_resolution(tmp_path, monkeypatch, public_dns, address):
    public_dns["box1.nyc3.cdn.digitaloceanspaces.com"] = ["93.184.216.34", address]
    fake = opener(monkeypatch, {CHARTS + "x.png": (200, _png())})
    assert scrape.download_images(None, _article(image_urls=[CHARTS + "x.png"]), tmp_path) == []
    assert fake.requests == []


def test_download_images_unresolvable_host_refused(tmp_path, monkeypatch, public_dns):
    public_dns["box1.nyc3.cdn.digitaloceanspaces.com"] = []
    opener(monkeypatch, {CHARTS + "x.png": (200, _png())})
    assert scrape.download_images(None, _article(image_urls=[CHARTS + "x.png"]), tmp_path) == []


def test_download_images_never_follows_redirects_and_skips_failures(tmp_path, monkeypatch):
    urls = [CHARTS + "moved.png", CHARTS + "gone.png", CHARTS + "ok.png"]
    opener(monkeypatch, {urls[0]: (302, b""), urls[1]: (404, b""), urls[2]: (200, _png())})
    assert len(scrape.download_images(None, _article(image_urls=urls), tmp_path)) == 1
    assert isinstance(scrape._PUBLIC_OPENER, FakeOpener)


def test_default_public_opener_carries_no_cookies_and_no_redirects():
    from research.marketdesk_auth import _NoRedirect
    import urllib.request
    handlers = scrape.build_opener().handlers
    assert any(isinstance(h, _NoRedirect) for h in handlers)
    assert not any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in handlers)


def test_download_images_honors_limit(tmp_path, monkeypatch):
    urls = [CHARTS + f"{i}.png" for i in range(5)]
    opener(monkeypatch, {u: (200, _png(color=(i, i, i))) for i, u in enumerate(urls)})
    assert len(scrape.download_images(None, _article(image_urls=urls), tmp_path, limit=2)) == 2


def test_download_images_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape, "MAX_IMAGE_BYTES", 10)
    opener(monkeypatch, {CHARTS + "big.png": (200, _png())})
    assert scrape.download_images(None, _article(image_urls=[CHARTS + "big.png"]), tmp_path) == []


# ---- to_png --------------------------------------------------------------------------------

def test_to_png_converts_jpeg_and_webp():
    for data, mode in ((JPEG_BYTES, "RGB"), (WEBP_BYTES, "RGBA")):
        out = scrape.to_png(data)
        assert out.startswith(b"\x89PNG\r\n\x1a\n")
        image = Image.open(io.BytesIO(out))
        assert image.format == "PNG" and image.mode == mode


def test_to_png_rejects_svg_garbage_and_oversize():
    assert scrape.to_png(SVG_BYTES) is None
    assert scrape.to_png(b"") is None
    assert scrape.to_png(b"not an image at all") is None
    assert scrape.to_png(JPEG_BYTES, max_bytes=len(JPEG_BYTES) - 1) is None
    assert scrape.to_png(JPEG_BYTES, max_pixels=64 * 48 - 1) is None
    assert scrape.to_png(JPEG_BYTES, max_pixels=64 * 48) is not None


def test_to_png_rejects_pdf_bytes():
    assert scrape.to_png(PDF_BYTES) is None


@pytest.mark.parametrize("code,transient", [("unauthenticated", True), ("http_transport", True),
                                            ("http_503", True), ("http_429", True), ("http_404", False),
                                            ("not_pdf", False), ("bad_response", False)])
def test_scrape_error_transient_classification(code, transient):
    assert scrape.ScrapeError(code).transient is transient


def test_summary_input_preserves_a_trailing_hedge_beyond_old_display_cap():
    item = "A" * 700 + " but the forecast remains conditional on demand."
    assert scrape.parse_summary("- " + item) == [item]


def test_oversized_summary_input_is_rejected_instead_of_silently_cut():
    with pytest.raises(scrape.ScrapeError, match="summary_too_large"):
        scrape.parse_summary("- " + "x" * (scrape.MAX_SUMMARY_CHARS + 1))
