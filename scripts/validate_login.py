#!/usr/bin/env python3
"""Browser-login credential probe for MenthorQ and TheMarketEar.

Run as a subprocess by ``credential_validators`` so Playwright never runs
inside the API process. Credentials come from the environment; a throwaway
browser context is used so the production session jars are never touched.

stdout carries exactly one JSON verdict: {"status": ..., "message": ...}
where status is "valid" | "invalid" | "error". Progress goes to stderr.
Exit code 0 means "a verdict was produced", nonzero means the probe itself
could not run.
"""

from __future__ import annotations

import json
import os
import sys

NAV_TIMEOUT_MS = 45_000


def _verdict(status: str, message: str = "") -> None:
    print(json.dumps({"status": status, "message": message}))


def _check_menthorq() -> None:
    """Fresh WordPress login with a throwaway storage state."""
    import tempfile

    try:
        from clients.menthorq_client import MenthorQAuthError, MenthorQClient
    except ImportError:
        from scripts.clients.menthorq_client import (  # type: ignore
            MenthorQAuthError,
            MenthorQClient,
        )

    with tempfile.TemporaryDirectory(prefix="radon-cred-probe-") as tmp:
        try:
            client = MenthorQClient(
                headless=True,
                storage_state_path=os.path.join(tmp, "probe_storage_state.json"),
            )
            client.close()
        except MenthorQAuthError as exc:
            _verdict("invalid", f"MenthorQ login failed: {exc}")
            return
    _verdict("valid")


def _check_themarketear() -> None:
    """Replicates scripts/newsfeed/auth.js runLoginFlow in Python Playwright."""
    import re

    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    email = os.environ.get("THEMARKETEAR_EMAIL", "").strip()
    password = os.environ.get("THEMARKETEAR_PASSWORD", "").strip()
    if not email or not password:
        _verdict("error", "THEMARKETEAR_EMAIL / THEMARKETEAR_PASSWORD not set")
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        try:
            page.goto(
                "https://themarketear.com/",
                wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT_MS,
            )
            page.locator("header button.menu-item").first.click(
                force=True, timeout=15_000
            )
            panel = page.locator("#login-panel")
            panel.wait_for(state="visible", timeout=15_000)
            panel.get_by_role(
                "button", name=re.compile("sign in with email", re.I)
            ).click(timeout=15_000)
            email_input = panel.locator(
                'input[type="email"], input[name="email" i], input'
            ).first
            email_input.fill(email, timeout=15_000)
            panel.get_by_role("button", name=re.compile("^next$", re.I)).click(
                timeout=15_000
            )
            password_input = panel.locator('input[type="password"]').first
            password_input.fill(password, timeout=15_000)
            password_input.press("Enter")
            try:
                page.wait_for_url(re.compile(r"/newsfeed"), timeout=30_000)
            except PlaywrightTimeout:
                pass
            url = page.url
            if "/newsfeed" in url and "/login" not in url:
                _verdict("valid")
            else:
                _verdict(
                    "invalid",
                    f"TheMarketEar login finished on an unexpected page: {url}",
                )
        except PlaywrightTimeout as exc:
            _verdict("invalid", f"TheMarketEar login flow stalled: {exc}")
        finally:
            browser.close()


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("menthorq", "themarketear"):
        print("usage: validate_login.py {menthorq|themarketear}", file=sys.stderr)
        return 2
    service = sys.argv[1]
    try:
        if service == "menthorq":
            _check_menthorq()
        else:
            _check_themarketear()
    except Exception as exc:  # noqa: BLE001 - probe failure, not a verdict
        print(f"probe crashed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
