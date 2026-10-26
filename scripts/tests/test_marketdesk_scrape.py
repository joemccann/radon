"""MarketDesk scraper: library/article parsing, PDF + image download guards, PNG re-encode, Chromium launch.

Parser tests run on SYNTHETIC fixtures (see fixtures/marketdesk/README.md). No network: the browser
request context is a fake and DNS goes through a monkeypatched resolver.
"""
import io
import json
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
REF = scrape.ArticleRef(
    article_id="4AJs5Adl2Yp",
    url="https://marketdesk.ai/library/browse/4AJs5Adl2Yp",
    published_at=datetime(2025, 10, 26, 12, 0, tzinfo=timezone.utc),
    title="Fed cuts and the curve",
)


def _png(size=(8, 8)):
    out = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Every host resolves public unless a test overrides it."""
    table = {}

    def resolve(host):
        return table.get(host, ["93.184.216.34"])

    monkeypatch.setattr(scrape, "_resolve_host", resolve)
    monkeypatch.delenv("MARKETDESK_ASSET_HOSTS", raising=False)
    return table


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def body(self):
        return self._body


class FakeRequest:
    def __init__(self, routes):
        self.routes = routes
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append({"url": url, **kwargs})
        if url not in self.routes:
            raise AssertionError(f"unexpected fetch {url}")
        return self.routes[url]


class FakeContext:
    def __init__(self, routes):
        self.request = FakeRequest(routes)


def _article(**overrides):
    base = dict(ref=REF, title="t", publisher=None, summary=[], body_text="", image_urls=[],
                pdf_href="/standalone/4AJs5Adl2Yp")
    base.update(overrides)
    return scrape.Article(**base)


# ---- parse_library -------------------------------------------------------------------------

def test_parse_library_reads_unix_time_dedups_sorts_and_rejects_bad_ids():
    refs = scrape.parse_library((FIXTURES / "library.synthetic.html").read_text())
    assert [r.article_id for r in refs] == ["9QwErTy12Ab", "4AJs5Adl2Yp", "Zz9Yy8Xx7Ww6"]
    first = refs[0]
    assert first.url == "https://marketdesk.ai/library/browse/9QwErTy12Ab"
    assert first.published_at == datetime.fromtimestamp(1761566400, tz=timezone.utc)
    assert first.published_at.tzinfo is timezone.utc
    assert first.title == "Credit spreads widen"
    assert refs[1].title == "Fed cuts and the curve"  # first occurrence wins over the duplicate


def test_parse_library_handles_empty_and_landing_pages():
    assert scrape.parse_library("") == []
    assert scrape.parse_library((FIXTURES / "landing.html").read_text()) == []


def test_parse_library_respects_base_url_host():
    html = '<a href="https://marketdesk.ai/library/browse/AbCdEfGh12"><span time="1700000000"></span>x</a>'
    assert scrape.parse_library(html, base_url="https://staging.example") == []
    assert [r.url for r in scrape.parse_library(html)] == ["https://marketdesk.ai/library/browse/AbCdEfGh12"]


# ---- parse_article -------------------------------------------------------------------------

def test_parse_article_extracts_summary_images_and_preview_href():
    article = scrape.parse_article((FIXTURES / "article.synthetic.html").read_text(), REF)
    assert article.ref == REF
    assert article.title == "Fed cuts and the curve"
    assert article.publisher == "Goldman Sachs"
    assert article.summary == ["Front end prices 3 cuts by June.", "2s10s steepened 12bp on the week."]
    assert article.image_urls == [
        "https://marketdesk.ai/files/img/chart-1.jpg",
        "https://cdn.marketdesk.ai/img/chart-2.webp",
    ]
    assert article.pdf_href == "/standalone/4AJs5Adl2Yp"
    assert "The curve is doing the work." in article.body_text
    assert "4.25% terminal" in article.body_text
    assert "window.secret" not in article.body_text and "color:red" not in article.body_text


def test_parse_article_without_preview_has_no_pdf_and_falls_back_to_ref_title():
    article = scrape.parse_article("<html><body><main><p>just text</p></main></body></html>", REF)
    assert article.pdf_href is None
    assert article.title == REF.title
    assert article.publisher is None
    assert article.summary == []
    assert article.body_text == "just text"


def test_parse_article_without_an_article_container_refuses_the_whole_page():
    html = ('<body><header><span class="user">Joe McCann joseph@example.com</span></header>'
            '<div>Report text 5%</div><footer>acct</footer></body>')
    with pytest.raises(scrape.ScrapeError, match="no_article_body"):
        scrape.parse_article(html, REF)


def test_parse_article_strips_chrome_and_account_ui_from_body_text():
    html = ('<body><main><nav>Joe McCann joseph@example.com</nav><aside>Sidebar 99bp</aside>'
            '<div class="account-menu">Plan: Pro</div><span class="user-name">Joe</span>'
            '<p>Report text 5%</p><footer>acct</footer></main></body>')
    article = scrape.parse_article(html, REF)
    assert article.body_text == "Report text 5%"


# ---- download_pdf --------------------------------------------------------------------------

def test_download_pdf_direct_response(tmp_path):
    ctx = FakeContext({"https://marketdesk.ai/standalone/4AJs5Adl2Yp": FakeResponse(
        200, PDF_BYTES, {"Content-Type": "application/pdf"})})
    path = scrape.download_pdf(ctx, _article(), tmp_path)
    assert path == tmp_path / "4AJs5Adl2Yp.pdf"
    assert path.read_bytes() == PDF_BYTES
    assert ctx.request.gets[0]["max_redirects"] == 0


def test_download_pdf_viewer_page_follows_embedded_file_once(tmp_path):
    ctx = FakeContext({
        "https://marketdesk.ai/standalone/4AJs5Adl2Yp": FakeResponse(
            200, (FIXTURES / "standalone_viewer.synthetic.html").read_bytes(), {"Content-Type": "text/html"}),
        "https://marketdesk.ai/files/pdf/4AJs5Adl2Yp.pdf": FakeResponse(
            200, PDF_BYTES, {"Content-Type": "application/pdf"}),
    })
    path = scrape.download_pdf(ctx, _article(), tmp_path)
    assert path.read_bytes() == PDF_BYTES
    assert [g["url"] for g in ctx.request.gets] == [
        "https://marketdesk.ai/standalone/4AJs5Adl2Yp", "https://marketdesk.ai/files/pdf/4AJs5Adl2Yp.pdf"]


def test_download_pdf_viewer_page_does_not_recurse(tmp_path):
    viewer = (FIXTURES / "standalone_viewer.synthetic.html").read_bytes()
    ctx = FakeContext({
        "https://marketdesk.ai/standalone/4AJs5Adl2Yp": FakeResponse(200, viewer, {"Content-Type": "text/html"}),
        "https://marketdesk.ai/files/pdf/4AJs5Adl2Yp.pdf": FakeResponse(200, viewer, {"Content-Type": "text/html"}),
    })
    with pytest.raises(scrape.ScrapeError, match="not_pdf"):
        scrape.download_pdf(ctx, _article(), tmp_path)
    assert len(ctx.request.gets) == 2


def test_download_pdf_missing_preview_raises_no_pdf(tmp_path):
    ctx = FakeContext({})
    with pytest.raises(scrape.ScrapeError) as caught:
        scrape.download_pdf(ctx, _article(pdf_href=None), tmp_path)
    assert str(caught.value) == "no_pdf"
    assert ctx.request.gets == []


def test_download_pdf_rejects_wrong_magic(tmp_path):
    ctx = FakeContext({"https://marketdesk.ai/standalone/4AJs5Adl2Yp": FakeResponse(
        200, b"PK\x03\x04 zip", {"Content-Type": "application/pdf"})})
    with pytest.raises(scrape.ScrapeError, match="not_pdf"):
        scrape.download_pdf(ctx, _article(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_pdf_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape, "MAX_PDF_BYTES", 100)
    big = FakeResponse(200, PDF_BYTES, {"Content-Type": "application/pdf"})
    ctx = FakeContext({"https://marketdesk.ai/standalone/4AJs5Adl2Yp": big})
    with pytest.raises(scrape.ScrapeError, match="too_large"):
        scrape.download_pdf(ctx, _article(), tmp_path)
    declared = FakeResponse(200, b"%PDF-", {"Content-Type": "application/pdf", "Content-Length": "101"})
    ctx = FakeContext({"https://marketdesk.ai/standalone/4AJs5Adl2Yp": declared})
    with pytest.raises(scrape.ScrapeError, match="too_large"):
        scrape.download_pdf(ctx, _article(), tmp_path)


def test_download_pdf_refuses_off_host_href_before_fetch(tmp_path):
    ctx = FakeContext({})
    with pytest.raises(scrape.ScrapeError, match="host_refused"):
        scrape.download_pdf(ctx, _article(pdf_href="https://evil.example/x.pdf"), tmp_path)
    with pytest.raises(scrape.ScrapeError, match="host_refused"):
        scrape.download_pdf(ctx, _article(pdf_href="http://marketdesk.ai/standalone/x"), tmp_path)
    assert ctx.request.gets == []


def test_download_pdf_refuses_off_host_redirect(tmp_path):
    ctx = FakeContext({"https://marketdesk.ai/standalone/4AJs5Adl2Yp": FakeResponse(
        302, b"", {"Location": "https://evil.example/a.pdf"})})
    with pytest.raises(scrape.ScrapeError, match="host_refused"):
        scrape.download_pdf(ctx, _article(), tmp_path)
    assert len(ctx.request.gets) == 1


def test_download_pdf_allows_configured_asset_host_redirect(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETDESK_ASSET_HOSTS", "files.example-cdn.com")
    ctx = FakeContext({
        "https://marketdesk.ai/standalone/4AJs5Adl2Yp": FakeResponse(
            302, b"", {"Location": "https://files.example-cdn.com/a.pdf"}),
        "https://files.example-cdn.com/a.pdf": FakeResponse(200, PDF_BYTES, {"Content-Type": "application/pdf"}),
    })
    assert scrape.download_pdf(ctx, _article(), tmp_path).read_bytes() == PDF_BYTES


def test_download_pdf_http_error(tmp_path):
    ctx = FakeContext({"https://marketdesk.ai/standalone/4AJs5Adl2Yp": FakeResponse(500, b"")})
    with pytest.raises(scrape.ScrapeError, match="http_500"):
        scrape.download_pdf(ctx, _article(), tmp_path)


# ---- download_images -----------------------------------------------------------------------

def test_download_images_converts_dedupes_and_writes_png(tmp_path):
    urls = ["https://marketdesk.ai/a.jpg", "https://marketdesk.ai/b.webp",
            "https://marketdesk.ai/a-again.jpg", "https://marketdesk.ai/logo.svg"]
    ctx = FakeContext({
        urls[0]: FakeResponse(200, JPEG_BYTES, {"Content-Type": "image/jpeg"}),
        urls[1]: FakeResponse(200, WEBP_BYTES, {"Content-Type": "image/webp"}),
        urls[2]: FakeResponse(200, JPEG_BYTES, {"Content-Type": "image/jpeg"}),
        urls[3]: FakeResponse(200, SVG_BYTES, {"Content-Type": "image/svg+xml"}),
    })
    paths = scrape.download_images(ctx, _article(image_urls=urls), tmp_path)
    assert len(paths) == 2
    for path in paths:
        assert path.suffix == ".png" and path.parent == tmp_path
        data = path.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        import hashlib
        assert path.stem == hashlib.sha256(data).hexdigest()


def test_download_images_host_allowlist(tmp_path, monkeypatch):
    urls = ["https://evil.example/x.png", "http://marketdesk.ai/x.png",
            "https://cdn.marketdesk.ai/ok.png", "https://assets.example-cdn.com/ok2.png"]
    ctx = FakeContext({urls[2]: FakeResponse(200, _png()), urls[3]: FakeResponse(200, _png((9, 9)))})
    assert len(scrape.download_images(ctx, _article(image_urls=urls), tmp_path)) == 1
    monkeypatch.setenv("MARKETDESK_ASSET_HOSTS", "example-cdn.com")
    assert len(scrape.download_images(ctx, _article(image_urls=urls), tmp_path)) == 2
    assert {g["url"] for g in ctx.request.gets} == {urls[2], urls[3]}


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "100.100.1.1",
                                     "192.168.1.1", "::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_download_images_refuses_private_resolution(tmp_path, public_dns, address):
    public_dns["marketdesk.ai"] = ["93.184.216.34", address]
    url = "https://marketdesk.ai/x.png"
    ctx = FakeContext({url: FakeResponse(200, _png())})
    assert scrape.download_images(ctx, _article(image_urls=[url]), tmp_path) == []
    assert ctx.request.gets == []


def test_download_images_unresolvable_host_refused(tmp_path, public_dns):
    public_dns["marketdesk.ai"] = []
    url = "https://marketdesk.ai/x.png"
    ctx = FakeContext({url: FakeResponse(200, _png())})
    assert scrape.download_images(ctx, _article(image_urls=[url]), tmp_path) == []


def test_download_images_rechecks_each_redirect_hop(tmp_path, public_dns):
    public_dns["internal.marketdesk.ai"] = ["10.0.0.5"]
    ok, bad = "https://marketdesk.ai/ok.png", "https://marketdesk.ai/bad.png"
    ctx = FakeContext({
        ok: FakeResponse(301, b"", {"Location": "/real/ok.png"}),
        "https://marketdesk.ai/real/ok.png": FakeResponse(200, _png()),
        bad: FakeResponse(302, b"", {"Location": "https://internal.marketdesk.ai/x.png"}),
    })
    paths = scrape.download_images(ctx, _article(image_urls=[ok, bad]), tmp_path)
    assert len(paths) == 1
    assert "https://internal.marketdesk.ai/x.png" not in [g["url"] for g in ctx.request.gets]
    assert all(g["max_redirects"] == 0 for g in ctx.request.gets)


def test_download_images_redirect_hop_limit(tmp_path):
    routes = {f"https://marketdesk.ai/r{i}": FakeResponse(302, b"", {"Location": f"/r{i + 1}"}) for i in range(5)}
    ctx = FakeContext(routes)
    assert scrape.download_images(ctx, _article(image_urls=["https://marketdesk.ai/r0"]), tmp_path) == []
    assert len(ctx.request.gets) == 4  # initial + 3 hops, then give up


def test_download_images_honors_limit_and_skips_failures(tmp_path):
    urls = [f"https://marketdesk.ai/{i}.png" for i in range(6)]
    routes = {u: FakeResponse(200, _png((i + 2, i + 2))) for i, u in enumerate(urls)}
    routes[urls[0]] = FakeResponse(404, b"")
    routes[urls[1]] = FakeResponse(200, b"<html>not an image</html>")
    ctx = FakeContext(routes)
    paths = scrape.download_images(ctx, _article(image_urls=urls), tmp_path, limit=2)
    assert len(paths) == 2
    assert [g["url"] for g in ctx.request.gets] == urls[:4]


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


# ---- launch_browser ------------------------------------------------------------------------

class FakeChromium:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.launches = []

    def launch(self, **options):
        self.launches.append(options)
        if self.failures:
            raise self.failures.pop(0)
        return "BROWSER"


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


def test_launch_browser_env_allowlist_excludes_secrets(monkeypatch):
    for key, value in {"TURSO_AUTH_TOKEN": "t", "DROPBOX_WRITE_REFRESH_TOKEN": "d", "UW_TOKEN": "u",
                       "THEMARKETEAR_PASSWORD": "p", "MARKETDESK_FIREBASE_REFRESH_TOKEN": "r",
                       "PATH": "/usr/bin", "HOME": "/home/radon", "TZ": "UTC"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_SANDBOX", raising=False)
    chromium = FakeChromium()
    assert scrape.launch_browser(FakePlaywright(chromium)) == "BROWSER"
    options = chromium.launches[0]
    assert options["headless"] is True
    assert "--no-sandbox" not in options.get("args", [])
    assert "executable_path" not in options
    assert set(options["env"]) <= set(scrape.CHROMIUM_ENV_KEYS)
    assert {k: options["env"][k] for k in ("PATH", "HOME", "TZ")} == {"PATH": "/usr/bin", "HOME": "/home/radon", "TZ": "UTC"}
    assert not any(k.startswith(("TURSO_", "DROPBOX_", "UW_", "THEMARKETEAR_", "MARKETDESK_")) for k in options["env"])


def test_launch_browser_falls_back_to_no_sandbox_only_on_sandbox_failure(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_SANDBOX", raising=False)
    chromium = FakeChromium([RuntimeError("No usable sandbox! Update your kernel")])
    assert scrape.launch_browser(FakePlaywright(chromium)) == "BROWSER"
    assert "--no-sandbox" not in chromium.launches[0].get("args", [])
    assert "--no-sandbox" in chromium.launches[1]["args"]
    chromium = FakeChromium([RuntimeError("Executable doesn't exist")])
    with pytest.raises(RuntimeError, match="Executable"):
        scrape.launch_browser(FakePlaywright(chromium))
    assert len(chromium.launches) == 1


def test_launch_browser_uses_configured_executable(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "/opt/chrome/chrome")
    chromium = FakeChromium()
    scrape.launch_browser(FakePlaywright(chromium))
    assert chromium.launches[0]["executable_path"] == "/opt/chrome/chrome"


# ---- page-level wrappers -------------------------------------------------------------------

class FakePage:
    def __init__(self, html, url="https://marketdesk.ai/"):
        self.html = html
        self.url = url
        self.gotos = []
        self.waits = []

    def goto(self, url, **kwargs):
        self.gotos.append(url)

    def wait_for_selector(self, selector, **kwargs):
        self.waits.append(selector)

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def evaluate(self, script, *args):
        self.evaluates = getattr(self, "evaluates", 0) + 1
        counts = getattr(self, "counts", None)
        return counts.pop(0) if counts else 0

    def wait_for_timeout(self, ms):
        self.sleeps = getattr(self, "sleeps", 0) + 1

    def content(self):
        return self.html


def test_list_latest_navigates_library_and_parses():
    page = FakePage((FIXTURES / "library.synthetic.html").read_text())
    refs = scrape.list_latest(page)
    assert page.gotos == [scrape.LIBRARY_URL]
    assert page.waits == [scrape.SELECTORS["library_item"]]
    assert refs[0].article_id == "9QwErTy12Ab"


def test_list_latest_landing_raises_unauthenticated():
    page = FakePage((FIXTURES / "landing.html").read_text())
    with pytest.raises(scrape.ScrapeError, match="unauthenticated"):
        scrape.list_latest(page)


def test_fetch_article_refuses_non_canonical_ref():
    bad = scrape.ArticleRef("4AJs5Adl2Yp", "https://evil.example/library/browse/4AJs5Adl2Yp", REF.published_at, "x")
    page = FakePage("")
    with pytest.raises(scrape.ScrapeError, match="bad_ref"):
        scrape.fetch_article(page, bad)
    assert page.gotos == []


def test_fetch_article_navigates_and_parses():
    page = FakePage((FIXTURES / "article.synthetic.html").read_text())
    article = scrape.fetch_article(page, REF)
    assert page.gotos == [REF.url]
    assert article.pdf_href == "/standalone/4AJs5Adl2Yp"


def test_selectors_are_one_dict_with_required_keys():
    for key in ("landing", "app_shell", "library_item", "item_time", "item_title", "article_title",
                "article_publisher", "summary_items", "article_images", "pdf_preview"):
        assert isinstance(scrape.SELECTORS[key], str) and scrape.SELECTORS[key]
    assert scrape.SELECTORS["pdf_preview"] == "a.action.preview"
    json.dumps(scrape.SELECTORS)


# ---- review fixes ------------------------------------------------------------------------------

def test_parse_library_with_anchors_but_no_times_raises_library_unparsed():
    html = ('<main><a href="/library/browse/AbCdEfGh12">One</a>'
            '<a href="/library/browse/ZyXwVuTs98">Two</a></main>')
    with pytest.raises(scrape.ScrapeError, match="library_unparsed"):
        scrape.parse_library(html)


def test_list_latest_scrolls_until_the_list_stops_growing():
    page = FakePage((FIXTURES / "library.synthetic.html").read_text())
    page.counts = [7, 12, 12]
    scrape.list_latest(page)
    assert page.evaluates == 3


def test_list_latest_scroll_is_bounded():
    page = FakePage((FIXTURES / "library.synthetic.html").read_text())
    page.counts = list(range(1, 100))
    scrape.list_latest(page)
    assert page.evaluates == scrape.MAX_LIBRARY_SCROLLS


def test_fetch_article_unrendered_page_is_a_transient_error():
    page = FakePage((FIXTURES / "article.synthetic.html").read_text())

    def never(selector, **kwargs):
        raise TimeoutError("Timeout 30000ms exceeded")

    page.wait_for_selector = never
    with pytest.raises(scrape.ScrapeError, match="article_unrendered") as info:
        scrape.fetch_article(page, REF)
    assert info.value.transient is True


def test_fetch_article_networkidle_timeout_still_parses():
    page = FakePage((FIXTURES / "article.synthetic.html").read_text())

    def busy(*args, **kwargs):
        raise TimeoutError("networkidle")

    page.wait_for_load_state = busy
    assert scrape.fetch_article(page, REF).pdf_href == "/standalone/4AJs5Adl2Yp"


@pytest.mark.parametrize(("code", "transient"), [
    ("http_503", True), ("http_500", True), ("http_429", True), ("http_transport", True),
    ("unauthenticated", True), ("article_unrendered", True),
    ("http_404", False), ("no_pdf", False), ("not_pdf", False), ("host_refused", False),
])
def test_scrape_error_transient_classification(code, transient):
    assert scrape.ScrapeError(code).transient is transient


def test_guarded_get_transport_error_drops_the_call_log(tmp_path):
    class Exploding:
        def get(self, url, **kwargs):
            raise RuntimeError("APIRequestContext.get: Timeout\nCall log:\n  - cookie: __session=SECRET")

    context = FakeContext({})
    context.request = Exploding()
    with pytest.raises(scrape.ScrapeError) as info:
        scrape.download_pdf(context, _article(), tmp_path)
    assert str(info.value) == "http_transport"
    assert info.value.__cause__ is None and info.value.__suppress_context__ is True
