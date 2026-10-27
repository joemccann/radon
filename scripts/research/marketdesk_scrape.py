"""MarketDesk JSON API client: latest listing, article summary/images/folders, PDF blob, PNG re-encode.

The marketdesk.ai web app is a thin client over a cookie-authenticated JSON API (captured 2026-10-26,
fixtures in scripts/tests/fixtures/marketdesk/api_*.json):

    POST /api/mobile/latest/latest  {"institutions": []}  -> [pathId, ...] newest first
    POST /api/mobile/items/provision {"pathIds": [...]}   -> [{pathId, name, t, type, institution, ...}]
    GET  /api/mobile/items/<id>/extra                    -> {summary: "- **Head**: text\n...", images: [...]}
    GET  /api/mobile/items/<id>/path                     -> [Current, 2026, October, Oct 26, <bank>, ...]
    GET  /files/<id>/blob                                -> the PDF (the /standalone/<id> viewer loads it)

Chart images are public objects at https://box1.nyc3.cdn.digitaloceanspaces.com/charts/<id>/<iid>,
fetched with a cookie-less opener that never follows redirects. Every image host must be on the
allowlist and resolve only to public addresses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import ipaddress
import logging
import os
from pathlib import Path
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from research.marketdesk_auth import BASE_URL, USER_AGENT, MarketDeskError, build_opener

log = logging.getLogger("research.marketdesk")

ScrapeError = MarketDeskError
CHART_HOST = "box1.nyc3.cdn.digitaloceanspaces.com"
CHART_BASE = f"https://{CHART_HOST}/charts/"
ARTICLE_ID_RE = re.compile(r"^[A-Za-z0-9]{8,16}$")
IMAGE_ID_RE = re.compile(r"^i-[0-9a-f]{64}\.(?:png|jpe?g|webp|gif)$")
LATEST_PATH = "/api/mobile/latest/latest"
PROVISION_PATH = "/api/mobile/items/provision"
PDF_TYPE = "application/pdf"
IMAGE_TIMEOUT = 30
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 20_000_000
MAX_IMAGE_PIXELS = 16_000_000
MAX_SUMMARY_ITEMS = 20
MAX_SUMMARY_CHARS = 500
MAX_TITLE_CHARS = 300
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RASTER_FORMATS = {"PNG", "JPEG", "GIF", "WEBP", "BMP"}
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_PUBLIC_OPENER = build_opener()  # no cookie jar: the session cookie never leaves marketdesk.ai


@dataclass(frozen=True)
class ArticleRef:
    article_id: str
    url: str
    published_at: datetime
    title: str
    institution: str | None = None


@dataclass(frozen=True)
class Article:
    ref: ArticleRef
    title: str
    publisher: str | None
    summary: list[str]
    image_urls: list[str]
    folders: list[str]


def _article_url(article_id: str) -> str:
    return f"{BASE_URL}/library/browse/{article_id}"


def _clean_text(value) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


# ---- parsers (pure) ------------------------------------------------------------------------

def parse_items(payload) -> list[ArticleRef]:
    """Provisioned items -> PDF refs, newest first. Rows without a valid id or unix time are dropped."""
    refs: dict[str, ArticleRef] = {}
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict) or row.get("type") != PDF_TYPE:
            continue
        article_id, stamp = row.get("pathId"), row.get("t")
        if not isinstance(article_id, str) or not ARTICLE_ID_RE.fullmatch(article_id) or article_id in refs:
            continue
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or stamp <= 0:
            continue
        title = _clean_text(row.get("name"))[:MAX_TITLE_CHARS] or article_id
        institution = _clean_text(row.get("institution"))[:120] or None
        refs[article_id] = ArticleRef(article_id, _article_url(article_id),
                                      datetime.fromtimestamp(stamp, timezone.utc), title, institution)
    return sorted(refs.values(), key=lambda ref: (ref.published_at, ref.article_id), reverse=True)


def parse_summary(text) -> list[str]:
    """Markdown bullets ("- **Head**: text") -> plain items ("Head: text"). Non-bullet text (MarketDesk's
    "**Document Missing**" placeholder for an unprocessed item) yields nothing."""
    items: list[str] = []
    for line in text.splitlines() if isinstance(text, str) else []:
        match = _BULLET_RE.match(line)
        if not match:
            continue
        item = _clean_text(match.group(1).replace("**", "").replace("__", ""))[:MAX_SUMMARY_CHARS]
        if item and item not in items:
            items.append(item)
        if len(items) == MAX_SUMMARY_ITEMS:
            break
    return items


def parse_images(rows, article_id: str) -> list[str]:
    """Chart image URLs for this item, page order, deduped. Ids are validated before any URL is built."""
    found = []
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict) or row.get("pathId") != article_id:
            continue
        iid = row.get("iid")
        if not isinstance(iid, str) or not IMAGE_ID_RE.fullmatch(iid):
            continue
        page = row.get("page") if isinstance(row.get("page"), int) else 0
        found.append((page, index, f"{CHART_BASE}{article_id}/{iid}"))
    urls: list[str] = []
    for _, _, url in sorted(found):
        if url not in urls:
            urls.append(url)
    return urls


def parse_folders(rows) -> list[str]:
    """Library folder chain without the "Current" root: [year, month, day, bank, desk...]."""
    folders = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("pathId") == "current":
            continue
        name = _clean_text(row.get("name"))
        if name:
            folders.append(name[:120])
    return folders


# ---- API calls -----------------------------------------------------------------------------

def list_latest(session) -> list[ArticleRef]:
    ids = session.api("POST", LATEST_PATH, {"institutions": []})
    if not isinstance(ids, list):
        raise ScrapeError("bad_response")
    ids = [i for i in ids if isinstance(i, str) and ARTICLE_ID_RE.fullmatch(i)]
    if not ids:
        return []
    return parse_items(session.api("POST", PROVISION_PATH, {"pathIds": ids}))


def fetch_article(session, ref: ArticleRef) -> Article:
    """Summary, chart images and folder chain. An unprocessed item has an empty summary (caller decides)."""
    if not ARTICLE_ID_RE.fullmatch(ref.article_id):
        raise ScrapeError("bad_ref")
    extra = session.api("GET", f"/api/mobile/items/{ref.article_id}/extra")
    if not isinstance(extra, dict):
        raise ScrapeError("bad_response")
    folders = parse_folders(session.api("GET", f"/api/mobile/items/{ref.article_id}/path"))
    # [year, month, day, bank, desk...]: the bank folder names the publisher better than the desk label.
    publisher = folders[3] if len(folders) > 3 else ref.institution
    return Article(ref, ref.title, publisher, parse_summary(extra.get("summary")),
                   parse_images(extra.get("images"), ref.article_id), folders)


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


def download_pdf(session, article: Article, dest: Path) -> Path:
    """GET /files/<id>/blob through the signed-in session. Writes dest/<id>.pdf (0600)."""
    data, _ = session.blob(f"/files/{article.ref.article_id}/blob", limit=MAX_PDF_BYTES)
    if not data.startswith(b"%PDF-"):
        raise ScrapeError("not_pdf")
    return _write_private(Path(dest) / f"{article.ref.article_id}.pdf", data)


# ---- public image fetch --------------------------------------------------------------------

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


def _allowed_hosts() -> list[str]:
    extra = [h.strip().lower() for h in os.environ.get("MARKETDESK_ASSET_HOSTS", "").split(",") if h.strip()]
    return [CHART_HOST, *extra]


def _url_allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in (None, 443):
        return False
    if not any(host == allowed or host.endswith("." + allowed) for allowed in _allowed_hosts()):
        return False
    addresses = _resolve_host(host)
    return bool(addresses) and all(is_public_address(a) for a in addresses)


def _public_get(url: str) -> bytes:
    if not _url_allowed(url):
        raise ScrapeError("host_refused")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _PUBLIC_OPENER.open(request, timeout=IMAGE_TIMEOUT) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise ScrapeError(f"http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ScrapeError("http_transport") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise ScrapeError("too_large")
    return data


def to_png(data: bytes, *, max_bytes: int | None = None, max_pixels: int = MAX_IMAGE_PIXELS) -> bytes | None:
    """Decode a raster image and re-encode as PNG. None for SVG, undecodable, or oversize input/output."""
    max_bytes = MAX_IMAGE_BYTES if max_bytes is None else max_bytes
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


def download_images(session, article: Article, dest: Path, *, limit: int = 40) -> list[Path]:
    """Fetch, PNG re-encode and store up to ``limit`` distinct images as dest/<sha256>.png. Skips failures.

    ``session`` is unused: chart images are public and must never receive the marketdesk.ai cookie."""
    del session
    saved, digests = [], set()
    for url in article.image_urls:
        if len(saved) >= limit:
            break
        try:
            data = _public_get(url)
        except ScrapeError as exc:
            log.info("marketdesk image skipped (%s): %s", exc, urllib.parse.urlsplit(url).hostname)
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
