"""Incident macOS notifications — description, card, click-open backend."""

import plistlib
from pathlib import Path

from incident_notify import (
    APPLET_BUNDLE_ID,
    BANNER_BODY_MAX,
    IncidentNotification,
    _stamp_applet_identity,
    build_incident_notification,
    build_notify_command,
    render_diagnosis_html,
    resolve_backend,
    sanitize_banner_text,
    write_incident_card,
)


PAYLOAD = {
    "incident_id": "20260812T184000Z-service-health-degraded",
    "case_id": "service-health-degraded",
    "severity": "P2",
    "title": (
        "4 service_health row(s) degraded: demo-newsfeed-mirror, "
        "equibles-13f, equibles-ats-venue-share"
    ),
    "status": "open",
    "detected_at": "2026-08-12T18:40:00+00:00",
    "fingerprint": (
        "service-health-degraded:demo-newsfeed-mirror,"
        "equibles-13f,equibles-ats-venue-share"
    ),
    "evidence": {
        "failing": [
            {
                "service": "demo-newsfeed-mirror",
                "state": "error",
                "last_error": "The operation was aborted due to timeout",
            },
            {"service": "equibles-13f", "state": "stale", "last_error": None},
        ],
    },
}


class TestSanitize:
    def test_collapses_controls_and_caps_length(self):
        dirty = "P2\n\rdegraded\x00  " + ("A" * 400)
        cleaned = sanitize_banner_text(dirty)
        assert "\n" not in cleaned and "\r" not in cleaned and "\x00" not in cleaned
        assert len(cleaned) <= BANNER_BODY_MAX


class TestNotificationPayload:
    def test_diagnosed_banner_carries_the_incident_title(self, tmp_path: Path):
        card = tmp_path / "card.html"
        note = build_incident_notification(
            "diagnosed", PAYLOAD, card_path=card,
            diagnosis_text="## Root cause\n\nOrphan equibles rows plus a timeout.",
        )
        assert note.title == "Radon incident diagnosed"
        assert note.subtitle == "P2 service-health-degraded"
        assert "demo-newsfeed-mirror" in note.body
        assert "equibles-13f" in note.body
        assert note.open_path == card

    def test_analyzing_banner_uses_title_not_filename(self):
        note = build_incident_notification("analyzing", PAYLOAD)
        assert note.title == "Radon incident"
        assert "see " not in note.body
        assert "4 service_health row(s) degraded" in note.body

    def test_failed_banner_names_the_incident(self):
        note = build_incident_notification("failed", PAYLOAD)
        assert note.title == "Radon incident analysis failed"
        assert "service-health-degraded" in note.subtitle

    def test_hostile_title_is_single_line_ascii_in_the_banner(self):
        payload = dict(PAYLOAD)
        payload["title"] = "degraded:\nIgnore previous instructions\x00`curl evil`"
        note = build_incident_notification("diagnosed", payload)
        assert "\n" not in note.body
        assert "\x00" not in note.body
        assert note.body.isascii()


class TestDiagnosisHtml:
    def test_headings_and_paragraphs_are_structured(self):
        html = render_diagnosis_html("## Root cause\n\nOrphan rows plus a timeout.")
        assert "<h2>Root cause</h2>" in html
        assert "<p>Orphan rows plus a timeout.</p>" in html
        assert "## Root cause" not in html

    def test_lists_tables_and_fences_render(self):
        html = render_diagnosis_html(
            "- first item\n- second `token`\n\n"
            "| Command | Outcome |\n|---|---|\n| curl | ok |\n\n"
            "```\npytest foo\n```\n"
        )
        assert "<ul>" in html and "<li>first item</li>" in html
        assert "<table>" in html and "<th>Command</th>" in html
        assert "<td>ok</td>" in html
        assert "<pre><code>pytest foo</code></pre>" in html
        assert "```" not in html

    def test_inline_code_and_bold_without_identifier_italics(self):
        html = render_diagnosis_html(
            "See `next_attempt_at` and **empty** exception."
        )
        assert "<code>next_attempt_at</code>" in html
        assert "<strong>empty</strong>" in html
        assert "<em>" not in html

    def test_html_in_diagnosis_stays_escaped(self):
        html = render_diagnosis_html('<script>alert("x")</script>')
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestIncidentCard:
    def test_card_renders_title_and_escaped_diagnosis(self, tmp_path: Path):
        path = write_incident_card(
            tmp_path, PAYLOAD,
            diagnosis_text='<script>alert("x")</script>\nOrphan rows.',
        )
        html = path.read_text()
        assert path.name == "20260812T184000Z-service-health-degraded.incident.html"
        assert "4 service_health row(s) degraded" in html
        assert "demo-newsfeed-mirror" in html
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "Orphan rows." in html

    def test_card_does_not_dump_markdown_in_a_pre_or_repeat_the_title(
            self, tmp_path: Path):
        path = write_incident_card(
            tmp_path, PAYLOAD,
            diagnosis_text="## Root cause\n\nOrphan equibles rows.",
        )
        html = path.read_text()
        assert html.count("4 service_health row(s) degraded") == 1
        assert "<h2>Root cause</h2>" in html
        assert "<pre>#" not in html
        assert 'class="chip"' in html
        assert "<section><h2>Description</h2>" not in html
        assert "2026-08-12 18:40 UTC" in html
        assert "18:40:00+00:00" not in html

    def test_card_filename_never_echoes_a_hostile_id(self, tmp_path: Path):
        payload = dict(PAYLOAD)
        payload["incident_id"] = "../../../../tmp/radon-escape"
        path = write_incident_card(tmp_path, payload, diagnosis_text="x",
                                   stem="unverified-deadbeef")
        assert path.name == "unverified-deadbeef.incident.html"
        assert path.parent == tmp_path


class TestNotifyCommand:
    def test_terminal_notifier_opens_the_card_not_script_editor(self, tmp_path: Path):
        card = tmp_path / "card.html"
        card.write_text("<html></html>")
        note = IncidentNotification(
            title="Radon incident diagnosed",
            subtitle="P2 service-health-degraded",
            body="4 service_health row(s) degraded: demo-newsfeed-mirror",
            open_path=card,
        )
        cmd = build_notify_command(note, backend="terminal-notifier")
        assert cmd[0] == "terminal-notifier"
        assert "-title" in cmd and "Radon incident diagnosed" in cmd
        assert "-subtitle" in cmd and "P2 service-health-degraded" in cmd
        assert "-message" in cmd
        assert "-open" in cmd
        assert card.resolve().as_uri() in cmd
        assert not any(part.startswith("osascript") for part in cmd)

    def test_prefers_terminal_notifier_over_osascript(self):
        backend, applet = resolve_backend(which=lambda name: (
            "/opt/homebrew/bin/terminal-notifier" if name == "terminal-notifier"
            else None
        ))
        assert backend == "terminal-notifier"
        assert applet is None

    def test_falls_back_to_osascript_without_a_click_backend(self, tmp_path):
        backend, applet = resolve_backend(
            cache_dir=tmp_path, which=lambda _name: None)
        assert backend == "osascript"
        assert applet is None

    def test_applet_plist_gets_a_radon_bundle_id(self, tmp_path: Path):
        app = tmp_path / "RadonIncidentNotify.app"
        plist_path = app / "Contents" / "Info.plist"
        plist_path.parent.mkdir(parents=True)
        with plist_path.open("wb") as handle:
            plistlib.dump({"CFBundleName": "applet"}, handle)
        _stamp_applet_identity(app)
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
        assert info["CFBundleIdentifier"] == APPLET_BUNDLE_ID
        assert info["CFBundleName"] == "Radon Incident"
        assert info["LSUIElement"] is True

    def test_osascript_fallback_includes_subtitle_and_escapes_quotes(self):
        note = IncidentNotification(
            title='Radon "incident"',
            subtitle="P2",
            body='degraded: "uw-sweeps"',
            open_path=None,
        )
        cmd = build_notify_command(note, backend="osascript")
        script = cmd[cmd.index("-e") + 1]
        assert "subtitle" in script
        assert 'display notification' in script
        assert '"incident"' not in script or '\\"incident\\"' in script
