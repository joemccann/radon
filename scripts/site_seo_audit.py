#!/usr/bin/env python3
"""Generate a live SEO audit report for the marketing site."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


DEFAULT_AUDIT_URL = "http://127.0.0.1:3333"
DEFAULT_EXPECTED_SITE_URL = "https://radon.run"
REPORT_DIR = Path("reports")
BUILD_ROUTE_MAP = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/robots.txt": ("robots.txt.body", "text/plain; charset=utf-8"),
    "/sitemap.xml": ("sitemap.xml.body", "application/xml; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest.body", "application/manifest+json; charset=utf-8"),
    "/opengraph-image": ("opengraph-image.body", "image/png"),
}


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes
    error: str | None = None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class Finding:
    category: str
    check: str
    status: str
    evidence: str
    recommendation: str


class SeoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lang = ""
        self.title_parts: list[str] = []
        self.meta_names: dict[str, str] = {}
        self.meta_properties: dict[str, str] = {}
        self.links: list[dict[str, str]] = []
        self.anchor_hrefs: list[str] = []
        self.landmarks = {"header": 0, "nav": 0, "main": 0, "footer": 0}
        self.heading_counts = {"h1": 0, "h2": 0}
        self.images_total = 0
        self.images_with_alt = 0
        self._in_title = False
        self._in_jsonld = False
        self._jsonld_parts: list[str] = []
        self.structured_data: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): (value or "") for key, value in attrs}

        if tag == "html":
            self.lang = attrs_dict.get("lang", "")
        if tag in self.landmarks:
            self.landmarks[tag] += 1
        if tag in self.heading_counts:
            self.heading_counts[tag] += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            content = attrs_dict.get("content", "")
            if name:
                self.meta_names[name] = content
            if prop:
                self.meta_properties[prop] = content
        if tag == "link":
            rel = attrs_dict.get("rel", "")
            href = attrs_dict.get("href", "")
            self.links.append({"rel": rel, "href": href})
        if tag == "a":
            href = attrs_dict.get("href", "")
            if href:
                self.anchor_hrefs.append(href)
        if tag == "img":
            self.images_total += 1
            if attrs_dict.get("alt", "").strip():
                self.images_with_alt += 1
        if tag == "script" and attrs_dict.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_jsonld:
            self._jsonld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            raw = "".join(self._jsonld_parts).strip()
            if raw:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"@type": "INVALID_JSON_LD", "raw": raw[:160]}
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict):
                            self.structured_data.append(item)
                elif isinstance(payload, dict):
                    self.structured_data.append(payload)
            self._jsonld_parts = []

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()


def normalize_url(url: str) -> str:
    return url[:-1] if url.endswith("/") else url


def fetch(url: str) -> FetchResult:
    req = Request(url, headers={"User-Agent": "RadonSiteSeoAudit/1.0"})
    try:
        with urlopen(req, timeout=20) as response:
            return FetchResult(
                url=url,
                status=response.status,
                content_type=response.headers.get("Content-Type", ""),
                body=response.read(),
            )
    except HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        return FetchResult(
            url=url,
            status=exc.code,
            content_type=exc.headers.get("Content-Type", ""),
            body=body,
            error=str(exc),
        )
    except URLError as exc:
        return FetchResult(
            url=url,
            status=0,
            content_type="",
            body=b"",
            error=str(exc),
        )


def fetch_from_build(build_dir: Path, route: str) -> FetchResult:
    relative_name, content_type = BUILD_ROUTE_MAP[route]
    target = build_dir / relative_name
    if not target.exists():
        return FetchResult(
            url=f"build:{route}",
            status=0,
            content_type=content_type,
            body=b"",
            error=f"missing build artifact: {target}",
        )
    return FetchResult(
        url=f"build:{route}",
        status=200,
        content_type=content_type,
        body=target.read_bytes(),
    )


def pass_fail(condition: bool, category: str, check: str, evidence: str, recommendation: str) -> Finding:
    return Finding(
        category=category,
        check=check,
        status="PASS" if condition else "FAIL",
        evidence=evidence,
        recommendation=recommendation,
    )


def pass_warn_fail(
    *,
    passed: bool,
    warning: bool,
    category: str,
    check: str,
    evidence: str,
    recommendation: str,
) -> Finding:
    if passed:
        status = "PASS"
    elif warning:
        status = "WARN"
    else:
        status = "FAIL"
    return Finding(
        category=category,
        check=check,
        status=status,
        evidence=evidence,
        recommendation=recommendation,
    )


def analyze_homepage(html: str, expected_site_url: str) -> tuple[SeoHTMLParser, list[Finding]]:
    parser = SeoHTMLParser()
    parser.feed(html)

    title_len = len(parser.title)
    description = parser.meta_names.get("description", "")
    desc_len = len(description)
    canonical = ""
    manifest = ""
    for link in parser.links:
        rel_tokens = {token.strip().lower() for token in link["rel"].split()}
        if "canonical" in rel_tokens:
            canonical = link["href"]
        if "manifest" in rel_tokens:
            manifest = link["href"]

    structured_types = [item.get("@type", "UNKNOWN") for item in parser.structured_data]
    internal_links = [
        href
        for href in parser.anchor_hrefs
        if href.startswith("#") or href.startswith("/") or expected_site_url in href
    ]

    findings = [
        pass_fail(
            bool(parser.lang),
            "Indexation",
            "HTML language is declared",
            f"`lang={parser.lang or 'missing'}`",
            "Keep `lang=\"en\"` on the root document so search engines can classify language correctly.",
        ),
        pass_warn_fail(
            passed=30 <= title_len <= 65,
            warning=bool(parser.title),
            category="Metadata",
            check="Title tag length is in a healthy range",
            evidence=f"`{parser.title}` ({title_len} chars)",
            recommendation="Keep the homepage title present and roughly 30 to 65 characters for stable SERP rendering.",
        ),
        pass_warn_fail(
            passed=70 <= desc_len <= 170,
            warning=bool(description),
            category="Metadata",
            check="Meta description length is in a healthy range",
            evidence=f"`{description}` ({desc_len} chars)",
            recommendation="Maintain a descriptive meta description in the 70 to 170 character range.",
        ),
        pass_fail(
            normalize_url(canonical) == expected_site_url,
            "Metadata",
            "Canonical URL matches the production homepage",
            f"`{canonical or 'missing'}`",
            "Emit a single absolute canonical URL for the homepage so local and preview hosts do not compete for indexation.",
        ),
        pass_fail(
            parser.meta_names.get("robots", "").lower() in {"index, follow", "index,follow"},
            "Indexation",
            "Robots meta allows indexing and following",
            f"`{parser.meta_names.get('robots', 'missing')}`",
            "Keep the homepage explicitly indexable unless the site is intentionally being held back from search.",
        ),
        pass_fail(
            all(
                parser.meta_properties.get(key)
                for key in ("og:title", "og:description", "og:image", "og:url")
            ),
            "Social",
            "Open Graph card is complete",
            "Found og:title, og:description, og:image, and og:url."
            if all(
                parser.meta_properties.get(key)
                for key in ("og:title", "og:description", "og:image", "og:url")
            )
            else "One or more Open Graph tags are missing.",
            "Keep Open Graph title, description, image, and URL in the shared metadata source.",
        ),
        pass_fail(
            all(
                parser.meta_names.get(key)
                for key in ("twitter:card", "twitter:title", "twitter:description", "twitter:image")
            ),
            "Social",
            "Twitter card is complete",
            "Found twitter:card, twitter:title, twitter:description, and twitter:image."
            if all(
                parser.meta_names.get(key)
                for key in ("twitter:card", "twitter:title", "twitter:description", "twitter:image")
            )
            else "One or more Twitter card tags are missing.",
            "Publish a complete summary-large-image card so social shares stay consistent.",
        ),
        pass_fail(
            {"WebSite", "Organization", "SoftwareApplication"}.issubset(set(structured_types)),
            "Structured Data",
            "Structured data covers website, organization, and software",
            f"Types: {', '.join(structured_types) or 'none'}",
            "Keep JSON-LD for WebSite, Organization, and SoftwareApplication in the shared layout.",
        ),
        pass_fail(
            parser.heading_counts["h1"] == 1,
            "Content",
            "Homepage has exactly one H1",
            f"H1 count: {parser.heading_counts['h1']}",
            "Preserve a single top-level H1 so the landing page stays semantically coherent.",
        ),
        pass_fail(
            parser.heading_counts["h2"] >= 4,
            "Content",
            "Homepage sections expose secondary headings",
            f"H2 count: {parser.heading_counts['h2']}",
            "Keep section-level H2s so the single-page information architecture remains crawlable.",
        ),
        pass_fail(
            all(parser.landmarks[name] >= 1 for name in ("header", "nav", "main", "footer")),
            "Content",
            "Landmarks are present for major page regions",
            ", ".join(f"{name}={count}" for name, count in parser.landmarks.items()),
            "Keep semantic landmarks in place so crawlers and assistive tech can interpret the page structure.",
        ),
        pass_warn_fail(
            passed=len(internal_links) >= 5,
            warning=len(internal_links) >= 3,
            category="Internal Linking",
            check="Homepage exposes enough internal navigation paths",
            evidence=f"Internal links found: {len(internal_links)}",
            recommendation="Single-page sites still need anchor-based internal links across the header, hero, and footer.",
        ),
        pass_fail(
            parser.images_total == parser.images_with_alt,
            "Content",
            "All rendered images have alt text",
            f"Images with alt: {parser.images_with_alt}/{parser.images_total}",
            "Keep descriptive `alt` text on every rendered image, including brand assets.",
        ),
        pass_fail(
            manifest == "/manifest.webmanifest",
            "Metadata",
            "Web app manifest is linked from the homepage head",
            f"`{manifest or 'missing'}`",
            "Link the manifest through the shared metadata object so install metadata stays discoverable.",
        ),
    ]

    return parser, findings


def analyze_crawl_routes(
    robots_result: FetchResult,
    sitemap_result: FetchResult,
    manifest_result: FetchResult,
    og_result: FetchResult,
    expected_site_url: str,
) -> list[Finding]:
    findings: list[Finding] = []

    findings.append(
        pass_fail(
            robots_result.status == 200 and "sitemap.xml" in robots_result.text,
            "Crawlability",
            "robots.txt is reachable and advertises the sitemap",
            f"Status {robots_result.status}; content-type `{robots_result.content_type}`",
            "Serve `robots.txt` from the app and keep a sitemap reference in the file.",
        )
    )

    sitemap_ok = False
    sitemap_evidence = f"Status {sitemap_result.status}; content-type `{sitemap_result.content_type}`"
    if sitemap_result.status == 200:
        try:
            root = ET.fromstring(sitemap_result.body)
            namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            locations = [normalize_url(node.text or "") for node in root.findall("sm:url/sm:loc", namespace)]
            sitemap_ok = expected_site_url in locations
            sitemap_evidence = f"URLs: {', '.join(locations) or 'none'}"
        except ET.ParseError as exc:
            sitemap_evidence = f"XML parse error: {exc}"
    findings.append(
        pass_fail(
            sitemap_ok,
            "Crawlability",
            "sitemap.xml includes the production homepage",
            sitemap_evidence,
            "Keep the sitemap XML valid and pointed at the production canonical URL.",
        )
    )

    manifest_ok = (
        manifest_result.status == 200
        and "application/manifest+json" in manifest_result.content_type
    )
    findings.append(
        pass_fail(
            manifest_ok,
            "Crawlability",
            "manifest.webmanifest is reachable",
            f"Status {manifest_result.status}; content-type `{manifest_result.content_type}`",
            "Serve the web manifest from the App Router metadata route so install metadata stays in sync.",
        )
    )

    findings.append(
        pass_fail(
            og_result.status == 200 and og_result.content_type.startswith("image/"),
            "Social",
            "Social share image is reachable",
            f"Status {og_result.status}; content-type `{og_result.content_type}`",
            "Keep a first-party social image route in place for Open Graph and Twitter cards.",
        )
    )

    return findings


def render_report(
    *,
    audit_url: str,
    expected_site_url: str,
    generated_at: str,
    parser: SeoHTMLParser,
    findings: list[Finding],
    fetch_results: list[FetchResult],
) -> str:
    totals = {
        "PASS": sum(1 for item in findings if item.status == "PASS"),
        "WARN": sum(1 for item in findings if item.status == "WARN"),
        "FAIL": sum(1 for item in findings if item.status == "FAIL"),
    }

    endpoint_rows = "".join(
        f"""
        <tr>
          <td>{escape(result.url)}</td>
          <td>{result.status}</td>
          <td>{escape(result.content_type or 'n/a')}</td>
          <td>{escape(result.error or 'OK')}</td>
        </tr>
        """
        for result in fetch_results
    )

    finding_rows = "".join(
        f"""
        <tr>
          <td>{escape(item.category)}</td>
          <td>{escape(item.check)}</td>
          <td><span class="pill {item.status.lower()}">{item.status}</span></td>
          <td>{escape(item.evidence)}</td>
          <td>{escape(item.recommendation)}</td>
        </tr>
        """
        for item in findings
    )

    warnings_or_failures = [item for item in findings if item.status != "PASS"]
    recommendation_items = warnings_or_failures or findings[:3]
    recommendation_list = "".join(
        f"<li><strong>{escape(item.check)}:</strong> {escape(item.recommendation)}</li>"
        for item in recommendation_items
    )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Radon Site SEO Audit</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      :root {{
        --bg-canvas: #0a0f14;
        --bg-panel: #0f1519;
        --bg-panel-raised: #151c22;
        --line-grid: #1e293b;
        --text-primary: #f5f7fa;
        --text-secondary: #cbd5e1;
        --text-muted: #94a3b8;
        --signal-core: #05ad98;
        --warn: #f59e0b;
        --fail: #ef4444;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        background: var(--bg-canvas);
        color: var(--text-primary);
        font: 14px/1.6 Inter, system-ui, sans-serif;
      }}
      a {{ color: inherit; }}
      .wrap {{
        max-width: 1400px;
        margin: 0 auto;
        padding: 32px 20px 64px;
      }}
      .header {{
        border: 1px solid var(--line-grid);
        background: linear-gradient(180deg, var(--bg-panel-raised), var(--bg-panel));
        padding: 24px;
      }}
      .eyebrow {{
        color: var(--signal-core);
        font: 12px/1.4 "IBM Plex Mono", ui-monospace, monospace;
        letter-spacing: 0.18em;
        text-transform: uppercase;
      }}
      h1, h2 {{
        margin: 0;
        font-weight: 600;
      }}
      h1 {{
        margin-top: 12px;
        font-size: clamp(34px, 5vw, 54px);
        line-height: 1.04;
        max-width: 18ch;
      }}
      h2 {{
        font-size: 22px;
      }}
      p {{
        margin: 0;
        color: var(--text-secondary);
      }}
      .meta {{
        margin-top: 16px;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 12px;
      }}
      .card-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 12px;
        margin-top: 20px;
      }}
      .card, .panel {{
        border: 1px solid var(--line-grid);
        background: var(--bg-panel);
      }}
      .card {{
        padding: 16px;
      }}
      .metric {{
        color: var(--text-muted);
        font: 11px/1.4 "IBM Plex Mono", ui-monospace, monospace;
        letter-spacing: 0.18em;
        text-transform: uppercase;
      }}
      .metric-value {{
        margin-top: 10px;
        font-size: 28px;
        font-weight: 600;
      }}
      .layout {{
        display: grid;
        grid-template-columns: 1.15fr 0.85fr;
        gap: 20px;
        margin-top: 20px;
      }}
      .panel {{
        padding: 18px;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        margin-top: 12px;
      }}
      th, td {{
        border-top: 1px solid var(--line-grid);
        padding: 10px 8px;
        vertical-align: top;
        text-align: left;
      }}
      th {{
        color: var(--text-muted);
        font: 11px/1.4 "IBM Plex Mono", ui-monospace, monospace;
        letter-spacing: 0.18em;
        text-transform: uppercase;
      }}
      ul {{
        margin: 12px 0 0;
        padding-left: 18px;
        color: var(--text-secondary);
      }}
      .pill {{
        display: inline-flex;
        align-items: center;
        border: 1px solid var(--line-grid);
        border-radius: 999px;
        padding: 2px 8px;
        font: 11px/1.4 "IBM Plex Mono", ui-monospace, monospace;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }}
      .pill.pass {{ color: var(--signal-core); border-color: var(--signal-core); }}
      .pill.warn {{ color: var(--warn); border-color: var(--warn); }}
      .pill.fail {{ color: var(--fail); border-color: var(--fail); }}
      .kv {{
        display: grid;
        gap: 8px;
        margin-top: 12px;
      }}
      .kv div {{
        display: flex;
        justify-content: space-between;
        gap: 12px;
        border-top: 1px solid var(--line-grid);
        padding-top: 8px;
      }}
      @media (max-width: 980px) {{
        .layout {{
          grid-template-columns: 1fr;
        }}
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <section class="header">
        <div class="eyebrow">Site SEO Audit</div>
        <h1>Live crawlability, metadata, and share-surface verification for the Radon marketing site.</h1>
        <p>
          Audited <strong>{escape(audit_url)}</strong> against the expected production host
          <strong>{escape(expected_site_url)}</strong> on {escape(generated_at)}.
        </p>
        <div class="meta">
          <div class="card">
            <div class="metric">Title</div>
            <div class="metric-value">{escape(parser.title or "Missing")}</div>
          </div>
          <div class="card">
            <div class="metric">Canonical</div>
            <div class="metric-value">{escape(next((link["href"] for link in parser.links if "canonical" in link["rel"]), "Missing"))}</div>
          </div>
          <div class="card">
            <div class="metric">Structured Data</div>
            <div class="metric-value">{len(parser.structured_data)}</div>
          </div>
        </div>
        <div class="card-grid">
          <div class="card">
            <div class="metric">Pass</div>
            <div class="metric-value">{totals["PASS"]}</div>
          </div>
          <div class="card">
            <div class="metric">Warn</div>
            <div class="metric-value">{totals["WARN"]}</div>
          </div>
          <div class="card">
            <div class="metric">Fail</div>
            <div class="metric-value">{totals["FAIL"]}</div>
          </div>
        </div>
      </section>

      <div class="layout">
        <section class="panel">
          <h2>Findings</h2>
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th>Check</th>
                <th>Status</th>
                <th>Evidence</th>
                <th>Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {finding_rows}
            </tbody>
          </table>
        </section>

        <section class="panel">
          <h2>Recommendations</h2>
          <ul>
            {recommendation_list}
          </ul>

          <h2 style="margin-top: 24px;">Extracted Surface</h2>
          <div class="kv">
            <div><span>Description</span><span>{escape(parser.meta_names.get("description", "Missing"))}</span></div>
            <div><span>OG Image</span><span>{escape(parser.meta_properties.get("og:image", "Missing"))}</span></div>
            <div><span>Twitter Card</span><span>{escape(parser.meta_names.get("twitter:card", "Missing"))}</span></div>
            <div><span>H1/H2</span><span>{parser.heading_counts["h1"]}/{parser.heading_counts["h2"]}</span></div>
            <div><span>Internal Links</span><span>{len(parser.anchor_hrefs)}</span></div>
            <div><span>Images With Alt</span><span>{parser.images_with_alt}/{parser.images_total}</span></div>
          </div>

          <h2 style="margin-top: 24px;">Endpoint Checks</h2>
          <table>
            <thead>
              <tr>
                <th>URL</th>
                <th>Status</th>
                <th>Content Type</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {endpoint_rows}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  </body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_AUDIT_URL, help="Live site URL to audit.")
    parser.add_argument(
        "--expected-site-url",
        default=DEFAULT_EXPECTED_SITE_URL,
        help="Expected production URL used for canonical and sitemap validation.",
    )
    parser.add_argument(
        "--build-dir",
        help="Optional Next build artifact directory (for example `site/.next-build/server/app`).",
    )
    parser.add_argument("--output", help="Optional output report path.")
    parser.add_argument("--open", action="store_true", help="Open the generated report locally.")
    args = parser.parse_args()

    audit_url = normalize_url(args.url)
    expected_site_url = normalize_url(args.expected_site_url)

    if args.build_dir:
        build_dir = Path(args.build_dir)
        homepage = fetch_from_build(build_dir, "/")
        robots_result = fetch_from_build(build_dir, "/robots.txt")
        sitemap_result = fetch_from_build(build_dir, "/sitemap.xml")
        manifest_result = fetch_from_build(build_dir, "/manifest.webmanifest")
        og_result = fetch_from_build(build_dir, "/opengraph-image")
        audit_source = str(build_dir)
    else:
        homepage = fetch(f"{audit_url}/")
        robots_result = fetch(f"{audit_url}/robots.txt")
        sitemap_result = fetch(f"{audit_url}/sitemap.xml")
        manifest_result = fetch(f"{audit_url}/manifest.webmanifest")
        og_result = fetch(f"{audit_url}/opengraph-image")
        audit_source = audit_url

    if homepage.status != 200:
        print(f"failed to fetch homepage: {homepage.error or homepage.status}", file=sys.stderr)
        return 1

    html_parser, homepage_findings = analyze_homepage(homepage.text, expected_site_url)
    route_findings = analyze_crawl_routes(
        robots_result=robots_result,
        sitemap_result=sitemap_result,
        manifest_result=manifest_result,
        og_result=og_result,
        expected_site_url=expected_site_url,
    )
    findings = homepage_findings + route_findings

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    output_path = Path(args.output) if args.output else REPORT_DIR / (
        f"site-seo-audit-{datetime.now().strftime('%Y-%m-%d')}.html"
    )
    report_html = render_report(
        audit_url=audit_source,
        expected_site_url=expected_site_url,
        generated_at=generated_at,
        parser=html_parser,
        findings=findings,
        fetch_results=[homepage, robots_result, sitemap_result, manifest_result, og_result],
    )
    output_path.write_text(report_html, encoding="utf-8")

    if args.open:
        subprocess.run(["open", str(output_path)], check=False)

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
