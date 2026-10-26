"""MarketDesk sign-in bootstrap. Operator laptop only; never run from a unit.

    python scripts/research/marketdesk_bootstrap.py --from-cdp http://127.0.0.1:9222   # save refresh token
    python scripts/research/marketdesk_bootstrap.py --check                              # exchange, log in, list

The account has only the google.com Firebase provider, so the job cannot sign in by itself. Sign in to
marketdesk.ai with Google in a Chrome started with --remote-debugging-port, leave a marketdesk.ai tab
open, then run --from-cdp: it reads the Firebase refresh token out of that tab's IndexedDB
(firebaseLocalStorageDb) and writes it to a 0600 file. Copy that value into /etc/radon/env as
MARKETDESK_FIREBASE_REFRESH_TOKEN. Nothing secret is printed.
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

from research.marketdesk_auth import (  # noqa: E402
    BASE_URL, MarketDeskAuthError, MarketDeskError, MarketDeskSession, exchange_refresh_token,
)

DEFAULT_OUT = Path.home() / ".radon" / "marketdesk_refresh_token"
# Returns every record of firebaseLocalStorageDb/firebaseLocalStorage, or null.
READ_FIREBASE_DB = """() => new Promise(resolve => {
  const open = indexedDB.open('firebaseLocalStorageDb');
  open.onerror = () => resolve(null);
  open.onsuccess = () => {
    try {
      const read = open.result.transaction('firebaseLocalStorage', 'readonly')
        .objectStore('firebaseLocalStorage').getAll();
      read.onsuccess = () => resolve(read.result || []);
      read.onerror = () => resolve(null);
    } catch (e) { resolve(null); }
  };
})"""


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


def extract_refresh_token(records) -> str | None:
    """Refresh token from firebaseLocalStorage records (the signed-in user's stsTokenManager)."""
    return _find_refresh(records) if isinstance(records, list) else None


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


def check(path: Path, *, post=None, opener=None) -> int:
    """Exchange the saved token, log in to marketdesk.ai and list the latest items."""
    try:
        token = Path(path).read_text().strip()
        id_token, rotated = exchange_refresh_token(token, post=post)
        if rotated != token:
            write_token_file(path, rotated)
        session = MarketDeskSession({}, opener=opener, post=post)
        session.login(id_token)
        latest = session.api("POST", "/api/mobile/latest/latest", {"institutions": []})
    except OSError:
        print("failed: token file unreadable")
        return 1
    except (MarketDeskAuthError, MarketDeskError) as exc:
        print(f"failed: {exc}")
        return 1
    count = len(latest) if isinstance(latest, list) else 0
    print(f"ok: signed in to marketdesk.ai; {count} latest items listed")
    return 0


def from_cdp(endpoint: str, out: Path) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(endpoint)
        try:
            pages = [page for context in browser.contexts for page in context.pages
                     if page.url.startswith(BASE_URL + "/")]
            records = pages[0].evaluate(READ_FIREBASE_DB) if pages else None
        finally:
            browser.close()  # disconnects; the operator's Chrome keeps running
    if not pages:
        print("failed: no marketdesk.ai tab in that Chrome; sign in there first")
        return 1
    token = extract_refresh_token(records)
    if not token:
        print("failed: no signed-in Firebase user found in the marketdesk.ai tab")
        return 1
    write_token_file(out, token)
    print(f"ok: refresh token written to {out} (0600). Put its contents in /etc/radon/env as "
          "MARKETDESK_FIREBASE_REFRESH_TOKEN, then run with --check.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="0600 token file (default %(default)s)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="exchange the saved token, log in, list latest")
    mode.add_argument("--from-cdp", metavar="URL", help="Chrome DevTools endpoint, e.g. http://127.0.0.1:9222")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.out)
    return from_cdp(args.from_cdp, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
