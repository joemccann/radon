"""One-time MarketDesk sign-in bootstrap. Operator laptop only, headed; never run from a unit.

    python scripts/research/marketdesk_bootstrap.py            # sign in with Google, save refresh token
    python scripts/research/marketdesk_bootstrap.py --check    # exchange the saved token once: ok / failed

The account has only the google.com Firebase provider, so the scraper cannot sign in by itself. This
opens a real browser window on marketdesk.ai; the operator completes "Sign in with Google"; the script
reads the Firebase refresh token out of IndexedDB (firebaseLocalStorageDb) and writes it to a 0600 file.
Copy that value into /etc/radon/env as MARKETDESK_FIREBASE_REFRESH_TOKEN. Nothing secret is printed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.marketdesk_auth import BASE_URL, MarketDeskAuthError, exchange_refresh_token  # noqa: E402

DEFAULT_OUT = Path.home() / ".radon" / "marketdesk_refresh_token"
FIREBASE_DB = "firebaseLocalStorageDb"
SIGN_IN_TIMEOUT_MS = 10 * 60 * 1000


def _find_refresh(value):
    if isinstance(value, dict):
        manager = value.get("stsTokenManager")
        if isinstance(manager, dict) and isinstance(manager.get("refreshToken"), str) and manager["refreshToken"]:
            return manager["refreshToken"]
        for child in value.values():
            if (found := _find_refresh(child)) is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            if (found := _find_refresh(child)) is not None:
                return found
    elif isinstance(value, str) and "stsTokenManager" in value:
        try:
            return _find_refresh(json.loads(value))
        except ValueError:
            return None
    return None


def extract_refresh_token(state: dict) -> str | None:
    """Refresh token from a Playwright storage_state(indexed_db=True) dump, marketdesk.ai origin only."""
    for origin in state.get("origins", []) if isinstance(state, dict) else []:
        if not isinstance(origin, dict) or origin.get("origin") != BASE_URL:
            continue
        for database in origin.get("indexedDB", []) or []:
            if not isinstance(database, dict) or database.get("name") != FIREBASE_DB:
                continue
            for store in database.get("stores", []) or []:
                for record in (store or {}).get("records", []) or []:
                    if not isinstance(record, dict):
                        continue
                    found = _find_refresh(record.get("value"))
                    if found is None:
                        found = _find_refresh(record.get("valueEncoded"))
                    if found:
                        return found
    return None


def write_token_file(path: Path, token: str) -> Path:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".md-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(token.strip() + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def check(path: Path, *, post=None) -> int:
    try:
        token = Path(path).read_text().strip()
        _, rotated = exchange_refresh_token(token, post=post)
    except (OSError, MarketDeskAuthError) as exc:
        reason = str(exc) if isinstance(exc, MarketDeskAuthError) else "token file unreadable"
        print(f"failed: {reason}")
        return 1
    if rotated != token:
        write_token_file(path, rotated)
    print("ok: refresh token exchanged for an ID token")
    return 0


def sign_in(out: Path, *, channel: str | None) -> int:
    from playwright.sync_api import sync_playwright
    from research.marketdesk_scrape import SELECTORS

    with sync_playwright() as pw:
        options = {"headless": False, "ignore_default_args": ["--enable-automation"]}
        if channel:
            options["channel"] = channel
        browser = pw.chromium.launch(**options)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(BASE_URL + "/", wait_until="domcontentloaded")
            print("Complete 'Sign in with Google' in the browser window. Waiting up to 10 minutes.")
            page.wait_for_selector(SELECTORS["app_shell"], timeout=SIGN_IN_TIMEOUT_MS)
            # The landing redirects to / only after /api/user/login succeeds; Firebase state is on this origin.
            token = extract_refresh_token(context.storage_state(indexed_db=True))
        finally:
            browser.close()
    if not token:
        print("failed: signed in, but no Firebase refresh token was found in IndexedDB")
        return 1
    write_token_file(out, token)
    print(f"ok: refresh token written to {out} (0600). Put its contents in /etc/radon/env as "
          "MARKETDESK_FIREBASE_REFRESH_TOKEN, then run with --check.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="0600 token file (default %(default)s)")
    parser.add_argument("--check", action="store_true", help="exchange the saved token once and report")
    parser.add_argument("--channel", default=None,
                        help="browser channel, e.g. 'chrome' when Google refuses the bundled Chromium")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.out)
    return sign_in(args.out, channel=args.channel)


if __name__ == "__main__":
    raise SystemExit(main())
