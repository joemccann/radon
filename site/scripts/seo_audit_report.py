#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree


REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST_DIR = ".next-seo-audit"
DEFAULT_HOST = "127.0.0.1"


@dataclass
class AuditCheck:
    category: str
    name: str
    status: str
    evidence: str
    recommendation: str


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._inside_title = False
        self._inside_json_ld = False
        self._json_ld_parts: list[str] = []
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.anchors: list[dict[str, str]] = []
        self.landmarks = {"header": 0, "nav": 0, "main": 0, "footer": 0}
        self.h1_count = 0
        self.json_ld: list[Any] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): (value or "") for key, value in attrs}
        tag = tag.lower()
        if tag == "title":
            self._inside_title = True
        elif tag == "meta":
            self.metas.append(attr_map)
        elif tag == "link":
            self.links.append(attr_map)
        elif tag == "a":
            self.anchors.append(attr_map)
        elif tag in self.landmarks:
            self.landmarks[tag] += 1
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "script" and attr_map.get("type", "").lower() == "application/ld+json":
            self._inside_json_ld = True
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self.title += data
        if self._inside_json_ld:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._inside_title = False
        elif tag == "script" and self._inside_json_ld:
            raw = "".join(self._json_ld_parts).strip()
            if raw:
                try:
                    self.json_ld.append(json.loads(raw))
                except json.JSONDecodeError:
                    self.json_ld.append({"_invalid": raw})
            self._inside_json_ld = False
            self._json_ld_parts = []


def run_command(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def choose_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_HOST, 0))
        return int(sock.getsockname()[1])


def fetch(url: str, accept: str | None = None) -> tuple[int, bytes, dict[str, str]]:
    headers = {"User-Agent": "radon-seo-audit/1.0"}
    if accept:
        headers["Accept"] = accept
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            return int(response.status), response.read(), headers
    except HTTPError as error:
        headers = {key.lower(): value for key, value in error.headers.items()}
        return int(error.code), error.read(), headers
    except URLError as error:
        raise RuntimeError(f"Failed to fetch {url}: {error}") from error


def wait_for_server(url: str, timeout_seconds: int = 30) -> None:
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    while time.time() < deadline:
        try:
            status, _, _ = fetch(url)
            if status < 500:
                return
        except RuntimeError as error:
            last_error = str(error)
        time.sleep(0.5)
    raise RuntimeError(last_error or f"Timed out waiting for {url}")


def get_meta_value(metas: list[dict[str, str]], *, name: str | None = None, property_name: str | None = None) -> str:
    for meta in metas:
        if name and meta.get("name", "").lower() == name.lower():
            return meta.get("content", "")
        if property_name and meta.get("property", "").lower() == property_name.lower():
            return meta.get("content", "")
    return ""


def get_link_href(links: list[dict[str, str]], rel_name: str) -> str:
    target = rel_name.lower()
    for link in links:
        rel = link.get("rel", "").lower().split()
        if target in rel:
            return link.get("href", "")
    return ""


def json_ld_types(items: list[Any]) -> set[str]:
    discovered: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            value = item.get("@type")
            if isinstance(value, str):
                discovered.add(value)
            elif isinstance(value, list):
                discovered.update(entry for entry in value if isinstance(entry, str))
        elif isinstance(item, list):
            for entry in item:
                if isinstance(entry, dict):
                    value = entry.get("@type")
                    if isinstance(value, str):
                        discovered.add(value)
    return discovered


def classify(ok: bool, warning: bool = False) -> str:
    if ok:
        return "pass"
    if warning:
        return "warn"
    return "fail"


def audit_site(base_url: str) -> tuple[list[AuditCheck], dict[str, Any]]:
    root_status, root_body, root_headers = fetch(base_url)
    robots_status, robots_body, robots_headers = fetch(f"{base_url}/robots.txt", "text/plain")
    sitemap_status, sitemap_body, sitemap_headers = fetch(f"{base_url}/sitemap.xml", "application/xml")
    manifest_status, manifest_body, manifest_headers = fetch(
        f"{base_url}/manifest.webmanifest",
        "application/manifest+json",
    )
    og_status, _, og_headers = fetch(f"{base_url}/opengraph-image")
    twitter_status, _, twitter_headers = fetch(f"{base_url}/twitter-image")

    parser = DocumentParser()
    html_text = root_body.decode("utf-8", errors="replace")
    parser.feed(html_text)

    title = parser.title.strip()
    description = get_meta_value(parser.metas, name="description")
    canonical = get_link_href(parser.links, "canonical")
    expected_public_url = canonical.rstrip("/") if canonical else base_url
    robots_meta = get_meta_value(parser.metas, name="robots")
    og_title = get_meta_value(parser.metas, property_name="og:title")
    og_description = get_meta_value(parser.metas, property_name="og:description")
    og_image = get_meta_value(parser.metas, property_name="og:image")
    og_url = get_meta_value(parser.metas, property_name="og:url")
    twitter_card = get_meta_value(parser.metas, name="twitter:card")
    twitter_image = get_meta_value(parser.metas, name="twitter:image")
    theme_color = get_meta_value(parser.metas, name="theme-color")

    internal_anchor_count = sum(
        1
        for anchor in parser.anchors
        if anchor.get("href", "").startswith("#")
        or anchor.get("href", "").startswith(f"{base_url}#")
    )

    json_types = json_ld_types(parser.json_ld)
    robots_text = robots_body.decode("utf-8", errors="replace")

    sitemap_urls: list[str] = []
    try:
        root = ElementTree.fromstring(sitemap_body.decode("utf-8", errors="replace"))
        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        sitemap_urls = [node.text or "" for node in root.findall(".//sm:loc", namespace)]
    except ElementTree.ParseError:
        sitemap_urls = []

    manifest: dict[str, Any] = {}
    try:
        manifest = json.loads(manifest_body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        manifest = {}

    checks = [
        AuditCheck(
            "Metadata",
            "Title tag",
            classify(30 <= len(title) <= 65),
            f"{len(title)} chars: {title}",
            "Keep the title between roughly 30 and 65 characters with the core product terms intact.",
        ),
        AuditCheck(
            "Metadata",
            "Meta description",
            classify(120 <= len(description) <= 170),
            f"{len(description)} chars: {description}",
            "Keep the description specific and within common SERP truncation ranges.",
        ),
        AuditCheck(
            "Metadata",
            "Canonical link",
            classify(bool(canonical and canonical.startswith("http"))),
            canonical or "Missing",
            "Publish one absolute canonical URL for the landing page.",
        ),
        AuditCheck(
            "Metadata",
            "Robots meta",
            classify("index" in robots_meta.lower() and "follow" in robots_meta.lower()),
            robots_meta or "Missing",
            "Expose an explicit index/follow robots directive on the rendered document.",
        ),
        AuditCheck(
            "Social",
            "Open Graph card",
            classify(
                bool(og_title and og_description and og_image and og_url)
                and og_url.rstrip("/") == expected_public_url
            ),
            f"title={bool(og_title)} description={bool(og_description)} image={bool(og_image)} url={bool(og_url)}",
            "Ship a complete Open Graph card with title, description, URL, and image.",
        ),
        AuditCheck(
            "Social",
            "Twitter card",
            classify(twitter_card == "summary_large_image" and bool(twitter_image)),
            f"card={twitter_card or 'Missing'} image={twitter_image or 'Missing'}",
            "Use a large Twitter card with a dedicated preview image.",
        ),
        AuditCheck(
            "Structured Data",
            "JSON-LD entities",
            classify({"WebSite", "Organization", "SoftwareApplication"}.issubset(json_types)),
            ", ".join(sorted(json_types)) or "Missing",
            "Expose WebSite, Organization, and SoftwareApplication schema on the landing page.",
        ),
        AuditCheck(
            "Content",
            "Heading hierarchy",
            classify(parser.h1_count == 1),
            f"h1_count={parser.h1_count}",
            "Keep a single primary H1 and use section H2s below it.",
        ),
        AuditCheck(
            "Content",
            "Landmarks",
            classify(all(parser.landmarks[tag] >= 1 for tag in ("header", "nav", "main", "footer"))),
            ", ".join(f"{tag}={count}" for tag, count in parser.landmarks.items()),
            "Keep crawlable semantic landmarks in the rendered HTML.",
        ),
        AuditCheck(
            "Content",
            "Internal anchors",
            classify(internal_anchor_count >= 4, warning=internal_anchor_count >= 2),
            f"{internal_anchor_count} fragment links",
            "Retain enough same-page navigation to expose the main sections and reading path.",
        ),
        AuditCheck(
            "Discovery",
            "robots.txt",
            classify(robots_status == 200 and "sitemap:" in robots_text.lower()),
            f"status={robots_status} sitemap_line={'yes' if 'sitemap:' in robots_text.lower() else 'no'}",
            "Serve robots.txt and include the sitemap location.",
        ),
        AuditCheck(
            "Discovery",
            "sitemap.xml",
            classify(sitemap_status == 200 and expected_public_url in sitemap_urls),
            f"status={sitemap_status} urls={len(sitemap_urls)} expected={expected_public_url}",
            "Serve a sitemap that includes the canonical landing-page URL.",
        ),
        AuditCheck(
            "Discovery",
            "Manifest",
            classify(
                manifest_status == 200
                and manifest.get("theme_color") == "#0a0f14"
                and manifest.get("start_url") == "/"
            ),
            f"status={manifest_status} theme_color={manifest.get('theme_color')} start_url={manifest.get('start_url')}",
            "Publish a valid web manifest with the correct theme color and root start URL.",
        ),
        AuditCheck(
            "Assets",
            "Open Graph image route",
            classify(og_status == 200 and "image/png" in og_headers.get("content-type", "")),
            f"status={og_status} content_type={og_headers.get('content-type', 'Missing')}",
            "Serve a PNG Open Graph image from the metadata route.",
        ),
        AuditCheck(
            "Assets",
            "Twitter image route",
            classify(twitter_status == 200 and "image/png" in twitter_headers.get("content-type", "")),
            f"status={twitter_status} content_type={twitter_headers.get('content-type', 'Missing')}",
            "Serve a PNG Twitter image from the metadata route.",
        ),
        AuditCheck(
            "Assets",
            "Theme color",
            classify(theme_color.lower() == "#0a0f14"),
            theme_color or "Missing",
            "Expose the Radon canvas color as the browser theme color.",
        ),
    ]

    evidence = {
        "base_url": base_url,
        "root_status": root_status,
        "root_headers": root_headers,
        "robots_status": robots_status,
        "robots_headers": robots_headers,
        "sitemap_status": sitemap_status,
        "sitemap_headers": sitemap_headers,
        "manifest_status": manifest_status,
        "manifest_headers": manifest_headers,
        "title": title,
        "description": description,
        "canonical": canonical,
        "og_image": og_image,
        "twitter_image": twitter_image,
        "json_ld_types": sorted(json_types),
        "sitemap_urls": sitemap_urls,
        "manifest": manifest,
    }
    return checks, evidence


def render_report(checks: list[AuditCheck], evidence: dict[str, Any], report_path: Path) -> str:
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %I:%M %p %Z")
    counts = {
        "pass": sum(1 for item in checks if item.status == "pass"),
        "warn": sum(1 for item in checks if item.status == "warn"),
        "fail": sum(1 for item in checks if item.status == "fail"),
    }
    recommendations = [item for item in checks if item.status != "pass"]
    rows = "\n".join(
        f"""
        <tr>
          <td><span class="pill pill-{item.status}">{item.status.upper()}</span></td>
          <td>{html.escape(item.category)}</td>
          <td>{html.escape(item.name)}</td>
          <td>{html.escape(item.evidence)}</td>
          <td>{html.escape(item.recommendation)}</td>
        </tr>
        """
        for item in checks
    )
    recommendation_rows = "\n".join(
        f"""
        <tr>
          <td>{html.escape(item.category)}</td>
          <td>{html.escape(item.name)}</td>
          <td><span class="pill pill-{item.status}">{item.status.upper()}</span></td>
          <td>{html.escape(item.recommendation)}</td>
        </tr>
        """
        for item in recommendations
    ) or """
        <tr>
          <td colspan="4">No blocking recommendations. The rendered site met the current audit contract.</td>
        </tr>
    """

    evidence_blob = html.escape(json.dumps(evidence, indent=2, sort_keys=True))

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Radon Site SEO Audit</title>
    <style>
      :root {{
        color-scheme: dark;
        --bg-canvas: #0a0f14;
        --bg-panel: #0f1519;
        --bg-panel-raised: #151c22;
        --line-grid: #1e293b;
        --text-primary: #f5f7fa;
        --text-secondary: #cbd5e1;
        --text-muted: #94a3b8;
        --signal-core: #05ad98;
        --signal-warn: #d97706;
        --signal-negative: #dc2626;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: Inter, system-ui, sans-serif;
        background: var(--bg-canvas);
        color: var(--text-primary);
      }}
      a {{ color: inherit; }}
      .wrap {{
        max-width: 1320px;
        margin: 0 auto;
        padding: 32px 20px 48px;
      }}
      .hero,
      .panel {{
        border: 1px solid var(--line-grid);
        background: var(--bg-panel);
        border-radius: 4px;
      }}
      .hero {{
        padding: 28px;
      }}
      .eyebrow {{
        font: 600 11px/1.4 "IBM Plex Mono", ui-monospace, monospace;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--signal-core);
      }}
      h1 {{
        margin: 14px 0 10px;
        font-size: clamp(2rem, 4vw, 3.5rem);
        line-height: 1.02;
      }}
      p {{
        margin: 0;
        color: var(--text-secondary);
        line-height: 1.6;
      }}
      .meta {{
        margin-top: 18px;
        display: grid;
        gap: 12px;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      }}
      .card {{
        border: 1px solid var(--line-grid);
        background: var(--bg-panel-raised);
        padding: 14px 16px;
      }}
      .label {{
        font: 600 11px/1.4 "IBM Plex Mono", ui-monospace, monospace;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        color: var(--text-muted);
      }}
      .value {{
        margin-top: 8px;
        font-size: 1.7rem;
      }}
      .grid {{
        display: grid;
        gap: 20px;
        margin-top: 24px;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
      }}
      th, td {{
        padding: 14px 16px;
        border-top: 1px solid var(--line-grid);
        vertical-align: top;
        text-align: left;
      }}
      th {{
        font: 600 11px/1.4 "IBM Plex Mono", ui-monospace, monospace;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        color: var(--text-muted);
      }}
      .panel-title {{
        margin: 0;
        padding: 18px 20px;
        border-bottom: 1px solid var(--line-grid);
        font-size: 1.1rem;
      }}
      .pill {{
        display: inline-flex;
        align-items: center;
        padding: 4px 9px;
        border-radius: 999px;
        border: 1px solid var(--line-grid);
        font: 600 11px/1 "IBM Plex Mono", ui-monospace, monospace;
        letter-spacing: 0.12em;
      }}
      .pill-pass {{
        color: var(--signal-core);
        border-color: rgb(5 173 152 / 0.4);
      }}
      .pill-warn {{
        color: var(--signal-warn);
        border-color: rgb(217 119 6 / 0.4);
      }}
      .pill-fail {{
        color: var(--signal-negative);
        border-color: rgb(220 38 38 / 0.4);
      }}
      pre {{
        margin: 0;
        padding: 18px 20px;
        overflow: auto;
        background: var(--bg-panel-raised);
        color: var(--text-secondary);
        font: 500 12px/1.6 "IBM Plex Mono", ui-monospace, monospace;
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <section class="hero">
        <div class="eyebrow">Site Audit</div>
        <h1>Radon marketing-site SEO audit</h1>
        <p>Rendered against <strong>{html.escape(evidence["base_url"])}</strong> on {html.escape(generated_at)}. This report checks the live HTML, crawl routes, metadata routes, and manifest instead of only reading source files.</p>
        <div class="meta">
          <div class="card">
            <div class="label">Pass</div>
            <div class="value">{counts["pass"]}</div>
          </div>
          <div class="card">
            <div class="label">Warn</div>
            <div class="value">{counts["warn"]}</div>
          </div>
          <div class="card">
            <div class="label">Fail</div>
            <div class="value">{counts["fail"]}</div>
          </div>
          <div class="card">
            <div class="label">Report Path</div>
            <div class="value" style="font-size:1rem;">{html.escape(str(report_path))}</div>
          </div>
        </div>
      </section>

      <div class="grid">
        <section class="panel">
          <h2 class="panel-title">Recommendations</h2>
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th>Check</th>
                <th>Status</th>
                <th>Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {recommendation_rows}
            </tbody>
          </table>
        </section>

        <section class="panel">
          <h2 class="panel-title">Audit Matrix</h2>
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Category</th>
                <th>Check</th>
                <th>Evidence</th>
                <th>Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {rows}
            </tbody>
          </table>
        </section>

        <section class="panel">
          <h2 class="panel-title">Captured Evidence</h2>
          <pre>{evidence_blob}</pre>
        </section>
      </div>
    </div>
  </body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the Radon marketing site SEO surface.")
    parser.add_argument("--base-url", help="Audit an existing site instead of starting a local server.")
    parser.add_argument("--port", type=int, help="Local port to use when starting the site.")
    parser.add_argument("--no-build", action="store_true", help="Skip the local production build.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the generated report.")
    parser.add_argument("--output", help="Write the report to a custom path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server_process: subprocess.Popen[bytes] | None = None
    log_file = None

    try:
        if args.base_url:
            base_url = args.base_url.rstrip("/")
        else:
            env = os.environ.copy()
            env["NEXT_DIST_DIR"] = DEFAULT_DIST_DIR
            if not args.no_build:
                print("Building site for SEO audit...")
                run_command(["npm", "run", "build"], cwd=SITE_ROOT, env=env)

            port = args.port or choose_port()
            base_url = f"http://{DEFAULT_HOST}:{port}"
            log_path = REPO_ROOT / "tmp" / f"site-seo-server-{port}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "wb")
            server_process = subprocess.Popen(
                ["npm", "run", "start", "--", "--hostname", DEFAULT_HOST, "--port", str(port)],
                cwd=SITE_ROOT,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            print(f"Starting local site server on {base_url}...")
            wait_for_server(base_url)

        checks, evidence = audit_site(base_url)
        date_slug = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        report_path = Path(args.output) if args.output else REPO_ROOT / f"reports/site-seo-audit-{date_slug}.html"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(checks, evidence, report_path), encoding="utf-8")

        print(f"SEO audit report written to {report_path}")
        if not args.no_open:
            webbrowser.open(f"file://{report_path.resolve()}")

        fail_count = sum(1 for item in checks if item.status == "fail")
        warn_count = sum(1 for item in checks if item.status == "warn")
        print(f"Audit summary: {len(checks) - fail_count - warn_count} pass, {warn_count} warn, {fail_count} fail")
        return 0 if fail_count == 0 else 1
    finally:
        if server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()
        if log_file is not None:
            log_file.close()


if __name__ == "__main__":
    sys.exit(main())
