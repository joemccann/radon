"""MarketDesk library scraper: listing, article parse, PDF + image download, PNG re-encode.

Every selector below is UNVERIFIED until an authenticated probe captures the real library and article
DOM (see scripts/tests/fixtures/marketdesk/README.md). They live in SELECTORS so there is one place to fix.

Downloads go through the browser context's request API, so the marketdesk.ai session cookie rides
along only to marketdesk.ai (cookie domain scoping). Every URL and every redirect hop must be https,
on marketdesk.ai or a MARKETDESK_ASSET_HOSTS entry, and resolve only to public addresses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import io
import ipaddress
import logging
import os
from pathlib import Path
import re
import socket
import tempfile
import urllib.parse

from research.marketdesk_auth import BASE_URL

log = logging.getLogger("research.marketdesk")

SELECTORS = {  # UNVERIFIED until authed probe; single place to fix
    "landing": "#firebaseui-auth-container",
    "app_shell": "#app [data-v-app], #app[data-v-app], nav",
    "library_item": "a[href*='/library/browse/']",
    "item_time": "[time]",  # unix seconds attribute (verified in API.js client code)
    "item_title": ".title, h2, h3",
    "article_title": "h1",
    "article_publisher": ".publisher, .source",
    "summary_items": ".summary li",
    "article_body": "article, .article, main",
    "article_images": "img",
    "page_chrome": "nav, header, footer",
    # Never sent to a model: account UI on the signed-in shell (name, email, plan menus).
    "text_chrome": "nav, header, footer, aside, [class*=account], [class*=user], [class*=profile]",
    "pdf_preview": "a.action.preview",  # href /standalone/<id>
    "pdf_embed": "iframe[src], embed[src], object[data], a[href$='.pdf']",
}
LIBRARY_URL = os.environ.get("MARKETDESK_LIBRARY_URL") or BASE_URL + "/"
ARTICLE_ID_RE = re.compile(r"^[A-Za-z0-9]{8,16}$")
ARTICLE_PATH_RE = re.compile(r"^/library/browse/([^/?#]+)/?$")
NAV_TIMEOUT_MS = 30_000
REQUEST_TIMEOUT_MS = 60_000
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 20_000_000
MAX_IMAGE_PIXELS = 16_000_000
MAX_REDIRECTS = 3
MAX_SUMMARY_ITEMS = 20
MAX_SUMMARY_CHARS = 500
MAX_LIBRARY_SCROLLS = 6
SCROLL_SETTLE_MS = 750
# Scrolls to the bottom (lazy lists render more on scroll) and returns how many items are in the DOM.
SCROLL_SCRIPT = ("(selector) => { window.scrollTo(0, document.body.scrollHeight); "
                 "return document.querySelectorAll(selector).length; }")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RASTER_FORMATS = {"PNG", "JPEG", "GIF", "WEBP", "BMP"}

# Port of scripts/newsfeed/browser.js CHROMIUM_ENV_KEYS (DS-2026-10-05-04): renderers parse
# third-party pages, so Chromium gets only what it reads, never database or provider keys.
CHROMIUM_ENV_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
    "FONTCONFIG_PATH", "FONTCONFIG_FILE",
)
SANDBOX_START_FAILURE = re.compile(
    r"no usable sandbox|zygote|namespace sandbox|move to new namespace|setuid sandbox", re.I)


TRANSIENT_CODES = frozenset({"unauthenticated", "article_unrendered", "http_transport", "http_429"})


class ScrapeError(RuntimeError):
    """Short machine-readable reason ('no_pdf', 'not_pdf', 'host_refused', ...). Never a URL query or body."""

    @property
    def transient(self) -> bool:
        """True when a retry can succeed without a code or DOM change; such failures spend no attempt."""
        code = str(self)
        return code in TRANSIENT_CODES or bool(re.fullmatch(r"http_5\d\d", code))


@dataclass(frozen=True)
class ArticleRef:
    article_id: str
    url: str
    published_at: datetime
    title: str


@dataclass(frozen=True)
class Article:
    ref: ArticleRef
    title: str
    publisher: str | None
    summary: list[str]
    body_text: str
    image_urls: list[str]
    pdf_href: str | None


# ---- minimal DOM + CSS selector subset (stdlib only) ----------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}
NON_TEXT = {"script", "style", "noscript", "template", "svg"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent

    def elements(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.elements()

    def text(self, exclude=frozenset()):
        """Visible text; subtrees whose id() is in ``exclude`` are skipped."""
        parts = []

        def walk(node):
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag not in NON_TEXT and id(child) not in exclude:
                    walk(child)

        walk(self)
        return " ".join(" ".join(parts).split())

    def select(self, selector):
        groups = [_parse_selector(part) for part in selector.split(",") if part.strip()]
        return [el for el in self.elements() if any(_matches(el, steps) for steps in groups)]

    def select_one(self, selector):
        found = self.select(selector)
        return found[0] if found else None

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current)
        self.current.children.append(node)

    def handle_endtag(self, tag):
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html or "")
    builder.close()
    return builder.root


_COMPOUND_RE = re.compile(
    r"(?P<tag>^[a-zA-Z][a-zA-Z0-9-]*|^\*)|#(?P<id>[\w-]+)|\.(?P<cls>[\w-]+)"
    r"|\[(?P<attr>[\w-]+)(?:(?P<op>[\^$*]?=)(?P<q>['\"]?)(?P<val>.*?)(?P=q))?\]")


def _parse_compound(text):
    tests, pos = [], 0
    for match in _COMPOUND_RE.finditer(text):
        if match.start() != pos:
            raise ValueError(f"unsupported selector {text!r}")
        pos = match.end()
        if match.group("tag") and match.group("tag") != "*":
            tests.append(("tag", match.group("tag").lower()))
        elif match.group("id"):
            tests.append(("attr", "id", "=", match.group("id")))
        elif match.group("cls"):
            tests.append(("cls", match.group("cls")))
        elif match.group("attr"):
            tests.append(("attr", match.group("attr").lower(), match.group("op"), match.group("val")))
    if pos != len(text):
        raise ValueError(f"unsupported selector {text!r}")
    return tests


def _parse_selector(selector):
    """Return [(combinator, compound_tests)] left to right. Supports descendant and '>' combinators."""
    tokens = re.findall(r">|[^\s>]+(?:\[[^\]]*\])*[^\s>]*", selector.strip())
    steps, combinator = [], " "
    for token in tokens:
        if token == ">":
            combinator = ">"
            continue
        steps.append((combinator, _parse_compound(token)))
        combinator = " "
    return steps


def _compound_ok(node, tests):
    for test in tests:
        if test[0] == "tag" and node.tag != test[1]:
            return False
        if test[0] == "cls" and test[1] not in node.attrs.get("class", "").split():
            return False
        if test[0] == "attr":
            _, name, op, value = test
            if name not in node.attrs:
                return False
            actual = node.attrs[name]
            if op == "=" and actual != value:
                return False
            if op == "^=" and not (value and actual.startswith(value)):
                return False
            if op == "$=" and not (value and actual.endswith(value)):
                return False
            if op == "*=" and not (value and value in actual):
                return False
    return True


def _matches(node, steps):
    if not steps or not _compound_ok(node, steps[-1][1]):
        return False
    combinator = steps[-1][0]
    rest = steps[:-1]
    if not rest:
        return True
    if combinator == ">":
        return node.parent is not None and node.parent.tag != "#document" and _matches(node.parent, rest)
    return any(_matches(ancestor, rest) for ancestor in node.ancestors() if ancestor.tag != "#document")


# ---- pure parsers --------------------------------------------------------------------------

def _host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _canonical_article_url(article_id, base_url=BASE_URL):
    return base_url.rstrip("/") + "/library/browse/" + article_id


def _article_id_from_href(href, base_url):
    absolute = urllib.parse.urljoin(base_url.rstrip("/") + "/", href.strip())
    parts = urllib.parse.urlsplit(absolute)
    if parts.scheme != "https" or (parts.hostname or "").lower() != _host(base_url):
        return None
    match = ARTICLE_PATH_RE.match(parts.path)
    if not match or not ARTICLE_ID_RE.fullmatch(match.group(1)):
        return None
    return match.group(1)


def _unix_time(node):
    raw = node.attrs.get("time", "").strip()
    if not re.fullmatch(r"\d{9,11}", raw):
        return None
    return datetime.fromtimestamp(int(raw), tz=timezone.utc)


def _item_time(anchor, root):
    """[time] inside the anchor (or on it), else in the nearest ancestor that holds only this item."""
    candidates = [anchor] + anchor.select(SELECTORS["item_time"])
    for node in candidates:
        if "time" in node.attrs and (value := _unix_time(node)):
            return value
    for depth, ancestor in enumerate(anchor.ancestors()):
        if depth >= 3 or ancestor is root:
            break
        if len(ancestor.select(SELECTORS["library_item"])) != 1:
            break
        for node in ancestor.select(SELECTORS["item_time"]):
            if value := _unix_time(node):
                return value
    return None


def parse_library(html: str, base_url: str = BASE_URL) -> list[ArticleRef]:
    """Library HTML -> ArticleRefs, deduped by id (first wins), newest first. Items without a time are skipped.

    Article anchors present but none parseable means the [time] selector drifted: ScrapeError('library_unparsed').
    """
    root = parse_html(html)
    seen, refs, anchors = set(), [], 0
    for anchor in root.select(SELECTORS["library_item"]):
        article_id = _article_id_from_href(anchor.attrs.get("href", ""), base_url)
        if article_id is None or article_id in seen:
            continue
        anchors += 1
        published_at = _item_time(anchor, root)
        if published_at is None:
            log.warning("marketdesk library item %s has no [time]; skipped", article_id)
            continue
        seen.add(article_id)
        title_node = anchor.select_one(SELECTORS["item_title"])
        title = (title_node.text() if title_node else "") or anchor.text()
        refs.append(ArticleRef(article_id, _canonical_article_url(article_id, base_url), published_at, title))
    if anchors and not refs:
        raise ScrapeError("library_unparsed")
    refs.sort(key=lambda ref: (ref.published_at, ref.article_id), reverse=True)
    return refs


def _image_src(node):
    for name in ("src", "data-src", "data-original"):
        value = node.attrs.get(name, "").strip()
        if value:
            return value
    srcset = node.attrs.get("srcset", "").strip()
    return srcset.split(",")[0].split()[0] if srcset else ""


def parse_article(html: str, ref: ArticleRef, base_url: str = BASE_URL) -> Article:
    root = parse_html(html)
    page_url = ref.url or _canonical_article_url(ref.article_id, base_url)

    title_node = root.select_one(SELECTORS["article_title"])
    title = (title_node.text() if title_node else "") or ref.title
    publisher_node = root.select_one(SELECTORS["article_publisher"])
    publisher = (publisher_node.text() if publisher_node else "") or None

    summary = []
    for item in root.select(SELECTORS["summary_items"]):
        text = item.text()[:MAX_SUMMARY_CHARS]
        if text and text not in summary:
            summary.append(text)
        if len(summary) >= MAX_SUMMARY_ITEMS:
            break

    body_node = root.select_one(SELECTORS["article_body"])
    if body_node is None:  # never fall back to the whole signed-in page: it carries account UI
        raise ScrapeError("no_article_body")
    hidden = set()
    for node in body_node.select(SELECTORS["text_chrome"]):
        hidden.add(id(node))
    body_text = body_node.text(exclude=hidden)

    chrome = set()
    for node in root.select(SELECTORS["page_chrome"]):
        chrome.update(id(el) for el in node.elements())
    image_urls = []
    for img in root.select(SELECTORS["article_images"]):
        if id(img) in chrome:
            continue
        src = _image_src(img)
        if not src or src.lower().startswith("data:"):
            continue
        absolute = urllib.parse.urljoin(page_url, src)
        if urllib.parse.urlsplit(absolute).scheme in ("http", "https") and absolute not in image_urls:
            image_urls.append(absolute)

    preview = root.select_one(SELECTORS["pdf_preview"])
    pdf_href = (preview.attrs.get("href", "").strip() or None) if preview else None
    return Article(ref, title, publisher, summary, body_text, image_urls, pdf_href)


# ---- browser -------------------------------------------------------------------------------

def chromium_launch_options(*, sandbox: bool = True, headless: bool = True, env=None) -> dict:
    env = os.environ if env is None else env
    options = {"headless": headless, "env": {key: env[key] for key in CHROMIUM_ENV_KEYS if key in env}}
    executable = env.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "").strip()
    if executable:
        options["executable_path"] = executable
    if not sandbox:
        options["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
    return options


def launch_browser(pw, *, headless: bool = True):
    """Sandbox first; --no-sandbox only when Chromium itself says the sandbox cannot start."""
    configured = os.environ.get("PLAYWRIGHT_CHROMIUM_SANDBOX", "").strip().lower()
    sandbox = configured not in ("0", "false")
    try:
        return pw.chromium.launch(**chromium_launch_options(sandbox=sandbox, headless=headless))
    except Exception as exc:
        if not sandbox or not SANDBOX_START_FAILURE.search(str(exc)):
            raise
        log.error("marketdesk chromium sandbox unavailable, falling back to --no-sandbox: %s",
                  str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
        return pw.chromium.launch(**chromium_launch_options(sandbox=False, headless=headless))


def _on_landing(html):
    return parse_html(html).select_one(SELECTORS["landing"]) is not None


def list_latest(page) -> list[ArticleRef]:
    if _host(LIBRARY_URL) != _host(BASE_URL) or not LIBRARY_URL.startswith("https://"):
        raise ScrapeError("bad_library_url")
    page.goto(LIBRARY_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    try:
        page.wait_for_selector(SELECTORS["library_item"], timeout=NAV_TIMEOUT_MS)
    except Exception:
        html = page.content()
        raise ScrapeError("unauthenticated" if _on_landing(html) else "library_unavailable") from None
    _scroll_library(page)
    html = page.content()
    if _on_landing(html):
        raise ScrapeError("unauthenticated")
    return parse_library(html)


def _scroll_library(page) -> None:
    """Let a lazy-rendered list grow: scroll until the item count stops changing (bounded)."""
    previous = -1
    for _ in range(MAX_LIBRARY_SCROLLS):
        count = page.evaluate(SCROLL_SCRIPT, SELECTORS["library_item"])
        if not isinstance(count, int) or count <= previous:
            return
        previous = count
        page.wait_for_timeout(SCROLL_SETTLE_MS)


def fetch_article(page, ref: ArticleRef) -> Article:
    if not ARTICLE_ID_RE.fullmatch(ref.article_id) or ref.url != _canonical_article_url(ref.article_id):
        raise ScrapeError("bad_ref")
    page.goto(ref.url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    try:
        page.wait_for_selector(f"{SELECTORS['pdf_preview']}, {SELECTORS['article_title']}", timeout=NAV_TIMEOUT_MS)
    except Exception:
        # Neither title nor preview rendered: half-hydrated page or expired session, never a DOM verdict.
        raise ScrapeError("unauthenticated" if _on_landing(page.content()) else "article_unrendered") from None
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass  # the title or preview is up; parse what rendered
    html = page.content()
    if _on_landing(html):
        raise ScrapeError("unauthenticated")
    return parse_article(html, ref)


# ---- fetch guards --------------------------------------------------------------------------

def _resolve_host(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    return sorted({info[4][0] for info in infos})


def is_public_address(address: str) -> bool:
    """Port of scripts/newsfeed/media.js isPublicAddress (plus Python's IANA special-purpose table)."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _allowed_hosts():
    hosts = [_host(BASE_URL)]
    hosts += [h.strip().lower() for h in os.environ.get("MARKETDESK_ASSET_HOSTS", "").split(",") if h.strip()]
    return hosts


def _url_allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if parts.port not in (None, 443):
        return False
    if not any(host == allowed or host.endswith("." + allowed) for allowed in _allowed_hosts()):
        return False
    addresses = _resolve_host(host)
    return bool(addresses) and all(is_public_address(a) for a in addresses)


def _guarded_get(context, url: str, limit: int):
    """GET with manual redirects; every hop re-checked. Returns (bytes, content_type, final_url)."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _url_allowed(url):
            raise ScrapeError("host_refused")
        try:
            response = context.request.get(url, max_redirects=0, timeout=REQUEST_TIMEOUT_MS)
        except Exception:
            # Playwright's message carries a call log with the session cookie headers: never keep it.
            raise ScrapeError("http_transport") from None
        headers = {k.lower(): v for k, v in (response.headers or {}).items()}
        if 300 <= response.status < 400:
            location = headers.get("location", "").strip()
            if not location:
                raise ScrapeError(f"http_{response.status}")
            url = urllib.parse.urljoin(url, location)
            continue
        if response.status != 200:
            raise ScrapeError(f"http_{response.status}")
        declared = headers.get("content-length", "").strip()
        if declared.isdigit() and int(declared) > limit:
            raise ScrapeError("too_large")
        data = response.body()
        if len(data) > limit:
            raise ScrapeError("too_large")
        return data, headers.get("content-type", "").split(";")[0].strip().lower(), url
    raise ScrapeError("too_many_redirects")


def _write_private(path: Path, data: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".md-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _embedded_pdf_url(html: str, page_url: str) -> str | None:
    root = parse_html(html)
    for node in root.select(SELECTORS["pdf_embed"]):
        raw = node.attrs.get("src") or node.attrs.get("data") or node.attrs.get("href") or ""
        if not raw.strip():
            continue
        candidate = urllib.parse.urljoin(page_url, raw.strip())
        # pdf.js style viewers carry the document in ?file=
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(candidate).query)
        if query.get("file"):
            candidate = urllib.parse.urljoin(candidate, query["file"][0])
        return candidate
    return None


def download_pdf(context, article: Article, dest: Path) -> Path:
    """Fetch the preview target; follow one embedded PDF when it is a viewer page. Writes dest/<id>.pdf."""
    if not article.pdf_href:
        raise ScrapeError("no_pdf")
    url = urllib.parse.urljoin(article.ref.url, article.pdf_href)
    data, content_type, final_url = _guarded_get(context, url, MAX_PDF_BYTES)
    if not data.startswith(b"%PDF-") and ("html" in content_type or data.lstrip()[:1] == b"<"):
        embedded = _embedded_pdf_url(data.decode("utf-8", "replace"), final_url)
        if embedded is None:
            raise ScrapeError("not_pdf")
        data, _, _ = _guarded_get(context, embedded, MAX_PDF_BYTES)
    if not data.startswith(b"%PDF-"):
        raise ScrapeError("not_pdf")
    return _write_private(Path(dest) / f"{article.ref.article_id}.pdf", data)


def to_png(data: bytes, *, max_bytes: int = MAX_IMAGE_BYTES, max_pixels: int = MAX_IMAGE_PIXELS) -> bytes | None:
    """Decode a raster image and re-encode as PNG. None for SVG, undecodable, or oversize input/output."""
    if not data or len(data) > max_bytes:
        return None
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in RASTER_FORMATS:
                return None
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                return None
            image.seek(0)
            image.load()
            has_alpha = image.mode in ("RGBA", "LA", "PA") or (
                image.mode == "P" and "transparency" in image.info)
            converted = image.convert("RGBA" if has_alpha else "RGB")
            out = io.BytesIO()
            converted.save(out, "PNG")
    except Exception:
        return None
    png = out.getvalue()
    if len(png) > max_bytes or not png.startswith(PNG_SIGNATURE):
        return None
    return png


def download_images(context, article: Article, dest: Path, *, limit: int = 40) -> list[Path]:
    """Fetch, PNG re-encode and store up to ``limit`` distinct images as dest/<sha256>.png. Skips failures."""
    saved, digests = [], set()
    for url in article.image_urls:
        if len(saved) >= limit:
            break
        try:
            data, _, _ = _guarded_get(context, url, MAX_IMAGE_BYTES)
        except ScrapeError as exc:
            log.info("marketdesk image skipped (%s): %s", exc, _host(url))
            continue
        except Exception as exc:  # transport errors from Playwright
            log.info("marketdesk image skipped (%s): %s", type(exc).__name__, _host(url))
            continue
        png = to_png(data)
        if png is None:
            continue
        digest = hashlib.sha256(png).hexdigest()
        if digest in digests:
            continue
        digests.add(digest)
        saved.append(_write_private(Path(dest) / f"{digest}.png", png))
    return saved
