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
    ))
    return path


def _render_card_html(*, title: str, subtitle: str, incident_id: str,
                      services: list[str], diagnosis: str) -> str:
    service_line = ", ".join(services) if services else "none listed"
    return (
        "<!doctype html>\n<meta charset=\"utf-8\">\n"
        f"<title>{html.escape(subtitle)}</title>\n"
        "<style>\n"
        "body{background:#0a0f14;color:#e2e8f0;font-family:Inter,ui-sans-serif,"
        "system-ui,sans-serif;margin:0}\n"
        "header{border-bottom:1px solid #1e293b;padding:16px 20px}\n"
        ".device{font-size:11px;letter-spacing:.08em;text-transform:uppercase;"
        "color:#758192}\n"
        "h1{font-size:18px;font-weight:500;margin:6px 0 8px}\n"
        ".badge{display:inline-block;border:1px solid #1e293b;border-radius:999px;"
        "padding:2px 8px;font-family:\"IBM Plex Mono\",ui-monospace,monospace;"
        "font-size:12px;color:#05AD98}\n"
        "main{padding:20px;max-width:720px}\n"
        "section{background:#0f1519;border:1px solid #1e293b;border-radius:4px;"
        "padding:16px;margin:0 0 12px}\n"
        "h2{font-size:11px;letter-spacing:.08em;text-transform:uppercase;"
        "color:#758192;margin:0 0 8px}\n"
        "p,pre{margin:0;white-space:pre-wrap;font-size:14px}\n"
        "pre{font-family:\"IBM Plex Mono\",ui-monospace,monospace;font-size:13px}\n"
        "</style>\n"
        "<header>\n"
        "<div class=\"device\">Radon incident</div>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        f"<span class=\"badge\">{html.escape(subtitle)}</span>\n"
        "</header>\n<main>\n"
        "<section><h2>Case</h2>"
        f"<p>{html.escape(incident_id)}</p></section>\n"
        "<section><h2>Description</h2>"
        f"<p>{html.escape(title)}</p></section>\n"
        "<section><h2>Failing services</h2>"
        f"<p>{html.escape(service_line)}</p></section>\n"
        "<section><h2>Diagnosis</h2>"
        f"<pre>{html.escape(diagnosis)}</pre></section>\n"
        "</main>\n"
    )


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
