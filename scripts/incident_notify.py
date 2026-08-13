"""Operator-facing macOS notifications for the laptop incident responder.

`osascript -e 'display notification'` is attributed to Script Editor. Clicking
that banner opens an empty Untitled document. This module posts through a
real app identity (terminal-notifier, or a compiled Radon applet) and always
carries an incident description. Click opens a local HTML card.

The agent projection still withholds `title` and other free text. This module
is the human path only.
"""

from __future__ import annotations

import html
import plistlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal, NamedTuple

BANNER_BODY_MAX = 180
CARD_DIAGNOSIS_MAX = 20000
APPLET_BUNDLE_ID = "com.radon.incident-notify"
APPLET_SOURCE = Path(__file__).resolve().parent / "macos" / "RadonIncidentNotify.applescript"
_NON_PRINTABLE_RE = re.compile(r"[^\x20-\x7e]+")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")

Kind = Literal["analyzing", "diagnosed", "failed"]


class IncidentNotification(NamedTuple):
    title: str
    subtitle: str
    body: str
    open_path: Path | None = None


def sanitize_banner_text(value: object, limit: int = BANNER_BODY_MAX) -> str:
    text = value if isinstance(value, str) else ""
    cleaned = _NON_PRINTABLE_RE.sub(" ", text)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        return cleaned[: limit - 3] + "..."
    return cleaned


def _failing_labels(payload: dict) -> list[str]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        return []
    failing = evidence.get("failing")
    if not isinstance(failing, list):
        return []
    labels = []
    for item in failing[:8]:
        if not isinstance(item, dict):
            continue
        service = item.get("service")
        if isinstance(service, str) and _TOKEN_RE.match(service):
            labels.append(service)
    return labels


def _description(payload: dict, diagnosis_text: str | None) -> str:
    title = sanitize_banner_text(payload.get("title"))
    if title:
        return title
    services = _failing_labels(payload)
    if services:
        return sanitize_banner_text("degraded: " + ", ".join(services))
    if diagnosis_text:
        for line in diagnosis_text.splitlines():
            stripped = sanitize_banner_text(line)
            if stripped and not stripped.startswith("#"):
                return stripped
    case_id = payload.get("case_id")
    if isinstance(case_id, str) and _TOKEN_RE.match(case_id):
        return case_id
    return "incident needs review"


def _subtitle(payload: dict) -> str:
    severity = payload.get("severity")
    case_id = payload.get("case_id")
    sev = severity if isinstance(severity, str) and re.match(r"^P[0-4]$", severity) else "P?"
    case = case_id if isinstance(case_id, str) and _TOKEN_RE.match(case_id) else "unknown-case"
    return f"{sev} {case}"


def build_incident_notification(
    kind: Kind,
    payload: dict,
    *,
    card_path: Path | None = None,
    diagnosis_text: str | None = None,
) -> IncidentNotification:
    titles = {
        "analyzing": "Radon incident",
        "diagnosed": "Radon incident diagnosed",
        "failed": "Radon incident analysis failed",
    }
    return IncidentNotification(
        title=titles[kind],
        subtitle=_subtitle(payload),
        body=_description(payload, diagnosis_text),
        open_path=card_path,
    )


def _display_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$",
        value,
    )
    if not match:
        return None
    return f"{match.group(1)} {match.group(2)} UTC"


def _card_filename(payload: dict, stem: str | None) -> str:
    if stem:
        return f"{stem}.incident.html"
    incident_id = payload.get("incident_id")
    if isinstance(incident_id, str) and re.match(
            r"^\d{8}T\d{6}Z-[a-z0-9-]{3,64}$", incident_id):
        return f"{incident_id}.incident.html"
    return "unverified.incident.html"


def write_incident_card(
    directory: Path,
    payload: dict,
    diagnosis_text: str = "",
    *,
    stem: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _card_filename(payload, stem)
    title = sanitize_banner_text(payload.get("title"), limit=240) or "Incident"
    subtitle = _subtitle(payload)
    incident_id = payload.get("incident_id")
    incident_label = (
        incident_id if isinstance(incident_id, str)
        and re.match(r"^\d{8}T\d{6}Z-[a-z0-9-]{3,64}$", incident_id)
        else "unverified"
    )
    status = payload.get("status")
    status_label = status if status in {"open", "resolved"} else None
    detected_label = _display_timestamp(payload.get("detected_at"))
    services = _failing_labels(payload)
    diagnosis = diagnosis_text[:CARD_DIAGNOSIS_MAX] if diagnosis_text else (
        "Diagnosis not written yet."
    )
    path.write_text(_render_card_html(
        title=title,
        subtitle=subtitle,
        incident_id=incident_label,
        services=services,
        diagnosis=diagnosis,
        status=status_label,
        detected_at=detected_label,
    ))
    return path


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_UL_RE = re.compile(r"^[-*]\s+(.+)$")
_OL_RE = re.compile(r"^(\d+)[.)]\s+(.+)$")
_HR_RE = re.compile(r"^-{3,}$")
_FENCE_RE = re.compile(r"^```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


def render_diagnosis_html(text: str) -> str:
    """Safe subset of markdown. Escape first; never emit raw input tags."""
    stripped = text.strip()
    if not stripped:
        return "<p>Diagnosis not written yet.</p>"
    return "\n".join(_render_block(block) for block in _diagnosis_blocks(stripped))


def _diagnosis_blocks(text: str) -> list[tuple[str, object]]:
    lines = text.splitlines()
    blocks: list[tuple[str, object]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _FENCE_RE.match(line):
            body, i = _collect_fence(lines, i)
            blocks.append(("fence", body))
            continue
        if line.lstrip().startswith("|"):
            rows, i = _collect_prefixed(lines, i, lambda item: item.lstrip().startswith("|"))
            blocks.append(("table", rows))
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(("heading", (len(heading.group(1)), heading.group(2))))
            i += 1
            continue
        if _HR_RE.match(line.strip()):
            blocks.append(("hr", None))
            i += 1
            continue
        if _UL_RE.match(line):
            rows, i = _collect_prefixed(lines, i, lambda item: _UL_RE.match(item) is not None)
            blocks.append(("ul", [_UL_RE.match(item).group(1) for item in rows]))
            continue
        if _OL_RE.match(line):
            rows, i = _collect_prefixed(lines, i, lambda item: _OL_RE.match(item) is not None)
            blocks.append(("ol", [_OL_RE.match(item).group(2) for item in rows]))
            continue
        para, i = _collect_paragraph(lines, i)
        blocks.append(("p", para))
    return blocks


def _collect_fence(lines: list[str], start: int) -> tuple[str, int]:
    body = []
    i = start + 1
    while i < len(lines) and not _FENCE_RE.match(lines[i]):
        body.append(lines[i])
        i += 1
    return "\n".join(body), i + 1 if i < len(lines) else i


def _collect_prefixed(lines: list[str], start: int, matches) -> tuple[list[str], int]:
    rows = []
    i = start
    while i < len(lines) and matches(lines[i]):
        rows.append(lines[i])
        i += 1
    return rows, i


def _collect_paragraph(lines: list[str], start: int) -> tuple[str, int]:
    rows = [lines[start]]
    i = start + 1
    while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
        rows.append(lines[i])
        i += 1
    return " ".join(item.strip() for item in rows), i


def _starts_block(line: str) -> bool:
    return bool(
        _FENCE_RE.match(line)
        or line.lstrip().startswith("|")
        or _HEADING_RE.match(line)
        or _HR_RE.match(line.strip())
        or _UL_RE.match(line)
        or _OL_RE.match(line)
    )


def _render_block(block: tuple[str, object]) -> str:
    kind, data = block
    if kind == "heading":
        level, text = data
        return f"<h{level}>{_inline(text)}</h{level}>"
    if kind == "p":
        return f"<p>{_inline(data)}</p>"
    if kind == "ul":
        items = "".join(f"<li>{_inline(item)}</li>" for item in data)
        return f"<ul>{items}</ul>"
    if kind == "ol":
        items = "".join(f"<li>{_inline(item)}</li>" for item in data)
        return f"<ol>{items}</ol>"
    if kind == "fence":
        return f"<pre><code>{html.escape(data)}</code></pre>"
    if kind == "table":
        return _render_table(data)
    if kind == "hr":
        return "<hr>"
    return f"<p>{_inline(str(data))}</p>"


def _render_table(rows: list[str]) -> str:
    parsed = [_split_table_row(row) for row in rows]
    parsed = [row for row in parsed if row and not _is_table_rule(row)]
    if not parsed:
        return ""
    head, body = parsed[0], parsed[1:]
    thead = "<thead><tr>" + "".join(f"<th>{_inline(cell)}</th>" for cell in head) + "</tr></thead>"
    tbody_rows = []
    for row in body:
        tbody_rows.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>")
    tbody = "<tbody>" + "".join(tbody_rows) + "</tbody>" if tbody_rows else ""
    return f"<table>{thead}{tbody}</table>"


def _split_table_row(row: str) -> list[str]:
    stripped = row.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_rule(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _inline(text: str) -> str:
    escaped = html.escape(text)
    holes: list[str] = []

    def stash(match: re.Match[str]) -> str:
        holes.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(holes) - 1}\x00"

    filled = _INLINE_CODE_RE.sub(stash, escaped)
    filled = _BOLD_RE.sub(r"<strong>\1</strong>", filled)
    filled = _ITALIC_RE.sub(r"<em>\1</em>", filled)
    for index, chunk in enumerate(holes):
        filled = filled.replace(f"\x00{index}\x00", chunk)
    return filled


def _severity_tone(subtitle: str) -> str:
    if subtitle.startswith(("P0", "P1")):
        return "fault"
    if subtitle.startswith("P2"):
        return "warn"
    return "signal"


def _render_card_html(*, title: str, subtitle: str, incident_id: str,
                      services: list[str], diagnosis: str,
                      status: str | None, detected_at: str | None) -> str:
    chips = "".join(
        f'<span class="chip">{html.escape(name)}</span>' for name in services
    ) or '<span class="chip muted">none listed</span>'
    facts = [f'<span class="id">{html.escape(incident_id)}</span>']
    if status:
        facts.append(f'<span class="status">{html.escape(status)}</span>')
    if detected_at:
        facts.append(f'<time>{html.escape(detected_at)}</time>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(subtitle)}</title>
<style>
:root {{
  --bg-canvas: #0a0f14;
  --bg-panel: #0f1519;
  --bg-raised: #151c22;
  --line-grid: #1e293b;
  --text-primary: #e2e8f0;
  --text-secondary: #94a3b8;
  --text-muted: #758192;
  --signal-core: #05AD98;
  --warn: #F5A623;
  --fault: #E85D6C;
}}
* {{ box-sizing: border-box; }}
html {{ font-size: 16px; }}
body {{
  margin: 0;
  background: var(--bg-canvas);
  color: var(--text-primary);
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
}}
.frame {{
  max-width: 46rem;
  margin: 0 auto;
  padding: 1.75rem 1.5rem 3rem;
}}
.mast {{
  border: 1px solid var(--line-grid);
  border-radius: 4px;
  background: var(--bg-panel);
  padding: 1rem 1.15rem 1.1rem;
}}
.device {{
  font-size: 0.6875rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-muted);
}}
.mast-row {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  margin-top: 0.45rem;
}}
h1 {{
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: -0.01em;
}}
.badge {{
  flex: 0 0 auto;
  display: inline-block;
  border: 1px solid var(--line-grid);
  border-radius: 999px;
  padding: 0.15rem 0.55rem;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.75rem;
  line-height: 1.4;
  color: var(--signal-core);
}}
.badge.warn {{ color: var(--warn); }}
.badge.fault {{ color: var(--fault); }}
.chips, .facts {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.85rem;
}}
.chip, .facts span, .facts time {{
  border: 1px solid var(--line-grid);
  border-radius: 999px;
  padding: 0.12rem 0.55rem;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.75rem;
  color: var(--text-secondary);
}}
.chip.muted {{ color: var(--text-muted); }}
.article {{
  margin-top: 1.75rem;
}}
.article-label {{
  font-size: 0.6875rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin: 0 0 0.85rem;
}}
.prose h1, .prose h2, .prose h3 {{
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin: 1.6rem 0 0.55rem;
}}
.prose h1:first-child, .prose h2:first-child, .prose h3:first-child {{ margin-top: 0; }}
.prose p, .prose li {{
  font-size: 1rem;
  line-height: 1.6;
  color: var(--text-primary);
}}
.prose p {{ margin: 0 0 0.9rem; }}
.prose ul, .prose ol {{
  margin: 0 0 1rem;
  padding-left: 1.2rem;
}}
.prose li {{ margin: 0 0 0.4rem; }}
.prose li::marker {{ color: var(--text-muted); }}
.prose code {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.84em;
  color: var(--signal-core);
}}
.prose pre {{
  margin: 0 0 1rem;
  padding: 0.85rem 1rem;
  background: var(--bg-raised);
  border: 1px solid var(--line-grid);
  border-radius: 4px;
  overflow-x: auto;
}}
.prose pre code {{
  font-size: 0.8125rem;
  line-height: 1.5;
  color: var(--text-primary);
  white-space: pre;
}}
.prose table {{
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 1.1rem;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.8125rem;
}}
.prose th, .prose td {{
  border: 1px solid var(--line-grid);
  padding: 0.45rem 0.55rem;
  text-align: left;
  vertical-align: top;
  line-height: 1.45;
  overflow-wrap: anywhere;
}}
.prose th {{
  color: var(--text-muted);
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  font-size: 0.6875rem;
}}
.prose hr {{
  border: 0;
  border-top: 1px solid var(--line-grid);
  margin: 1.25rem 0;
}}
.prose strong {{ font-weight: 600; }}
.prose em {{ font-style: italic; }}
</style>
</head>
<body>
<div class="frame">
  <header class="mast">
    <div class="device">Radon incident</div>
    <div class="mast-row">
      <h1>{html.escape(title)}</h1>
      <span class="badge {_severity_tone(subtitle)}">{html.escape(subtitle)}</span>
    </div>
    <div class="chips">{chips}</div>
    <div class="facts">{"".join(facts)}</div>
  </header>
  <article class="article">
    <p class="article-label">Diagnosis</p>
    <div class="prose">
{render_diagnosis_html(diagnosis)}
    </div>
  </article>
</div>
</body>
</html>
"""


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_notify_command(
    note: IncidentNotification,
    *,
    backend: str,
    applet: Path | None = None,
) -> list[str]:
    if backend == "terminal-notifier":
        cmd = [
            "terminal-notifier",
            "-title", note.title,
            "-subtitle", note.subtitle,
            "-message", note.body,
            "-group", APPLET_BUNDLE_ID,
        ]
        if note.open_path is not None:
            cmd.extend(["-open", note.open_path.resolve().as_uri()])
        return cmd
    if backend == "applet":
        if applet is None:
            raise ValueError("applet backend requires applet path")
        executable = applet / "Contents" / "MacOS" / "applet"
        return [str(executable), note.title, note.subtitle, note.body]
    script = (
        f"display notification {_applescript_string(note.body)} "
        f"with title {_applescript_string(note.title)} "
        f"subtitle {_applescript_string(note.subtitle)}"
    )
    return ["osascript", "-e", script]


def applet_dir(cache_dir: Path) -> Path:
    return cache_dir / "RadonIncidentNotify.app"


def ensure_applet(cache_dir: Path, *, which=shutil.which) -> Path | None:
    app = applet_dir(cache_dir)
    marker = app / "Contents" / "MacOS" / "applet"
    if marker.exists() and APPLET_SOURCE.exists():
        _stamp_applet_identity(app)
        return app
    if not APPLET_SOURCE.exists() or which("osacompile") is None:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    compiled = subprocess.run(
        ["osacompile", "-o", str(app), str(APPLET_SOURCE)],
        capture_output=True, timeout=20,
    )
    if compiled.returncode != 0 or not marker.exists():
        return None
    _stamp_applet_identity(app)
    return app


def _stamp_applet_identity(app: Path) -> None:
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.exists():
        return
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleIdentifier"] = APPLET_BUNDLE_ID
    info["CFBundleName"] = "Radon Incident"
    info["CFBundleDisplayName"] = "Radon Incident"
    info["LSUIElement"] = True
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle)


def _write_applet_click_target(app: Path, open_path: Path) -> None:
    resources = app / "Contents" / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "latest-open").write_text(str(open_path.resolve()) + "\n")


def resolve_backend(*, cache_dir: Path | None = None,
                    which=shutil.which) -> tuple[str, Path | None]:
    if which("terminal-notifier"):
        return "terminal-notifier", None
    if cache_dir is not None:
        app = ensure_applet(cache_dir, which=which)
        if app is not None:
            return "applet", app
    return "osascript", None


def notify(note: IncidentNotification, *,
           cache_dir: Path | None = None) -> str:
    backend, applet = resolve_backend(cache_dir=cache_dir)
    if backend == "applet" and applet is not None and note.open_path is not None:
        _write_applet_click_target(applet, note.open_path)
    cmd = build_notify_command(note, backend=backend, applet=applet)
    subprocess.run(cmd, capture_output=True, timeout=10)
    return backend
