"""Optional Cloudflare Workers AI report date for research.identify.

CLAIRIC_DATES_ENABLED and CLAIRIC_DATE_SANITY_ENABLED both default off.
Off, identify() keeps today's date ladder and writes no date_flag.
The HTTP call uses the stdlib (no new dependency). Credentials are
CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN from the process environment
(/etc/radon/env on the research host). A missing credential, timeout, or
API error logs one line and leaves the ladder date unchanged.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

API = "https://api.cloudflare.com/client/v4"
MODEL_PATH = "/ai/run/@cf/cloudflare/clairic"
PASS_TOKENS = 8192
CHARS_PER_TOKEN = 4
TIMEOUT_S = 10
MAX_RESPONSE_BYTES = 65_536
STALE_DAYS = 45
CONFIDENCE_MIN = 0.9
_ACCOUNT_RE = re.compile(r"[0-9a-fA-F]{32}")
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

REPORT_DATE_FIELD = {
    "type": "date",
    "instructions": (
        "Publication date of this report as printed on the document "
        "(cover, header or byline). Ignore data as-of dates, index base dates, "
        "copyright years, event dates, coverage periods and download or print dates."
    ),
}
PUBLISHER_FIELD = {
    "type": "text",
    "instructions": (
        "Name of the firm that published this research report: the legal owner "
        "in the copyright line or the brand on the first page. Not a company, "
        "data vendor or bank that the report merely cites."
    ),
}


class ClairicError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class MissingCredentials(ClairicError):
    def __init__(self):
        super().__init__("missing_credentials")


def _enabled(name, env):
    return (env.get(name) or "").strip() == "1"


def dates_enabled(env=None):
    import os
    return _enabled("CLAIRIC_DATES_ENABLED", os.environ if env is None else env)


def sanity_enabled(env=None):
    import os
    return _enabled("CLAIRIC_DATE_SANITY_ENABLED", os.environ if env is None else env)


def today_et():
    return datetime.now(ZoneInfo("America/New_York")).date()


def _schema_tokens(fields):
    return len(json.dumps(fields, separators=(",", ":"))) // CHARS_PER_TOKEN + 32


def _token_est(text):
    return len(text) // CHARS_PER_TOKEN


def build_state(pages, token_budget):
    """Pages 1-3 plus the last page, trimmed to the token budget. The last page is kept."""
    if not pages or token_budget <= 0:
        return ""
    keys = sorted(k for k in pages if isinstance(k, int))
    if not keys:
        return ""
    pick = list(dict.fromkeys(keys[:3] + ([keys[-1]] if len(keys) > 3 else [])))
    parts = [f"[page {k}]\n{str(pages[k]).strip()}" for k in pick]
    char_budget = token_budget * CHARS_PER_TOKEN
    if len(parts) < 2:
        return parts[0][:char_budget] if parts else ""
    last = parts[-1]
    head = "\n\n".join(parts[:-1])
    if len(last) >= char_budget:
        return last[:char_budget]
    room = char_budget - len(last) - 2
    if len(head) + 2 + len(last) <= char_budget:
        return head + "\n\n" + last
    return head[:room].rstrip() + "\n\n" + last


def build_request(pages, folder_date):
    """One pass when the state fits. Publisher rides along only if it stays in that pass."""
    date_only = {"report_date": REPORT_DATE_FIELD}
    both = {"report_date": REPORT_DATE_FIELD, "publisher": PUBLISHER_FIELD}
    date_budget = PASS_TOKENS - _schema_tokens(date_only)
    state = build_state(pages, date_budget)
    fields = both if state and _token_est(state) + _schema_tokens(both) <= PASS_TOKENS else date_only
    if fields is both and not state:
        fields = date_only
    body = {"model": "clairic", "state": state, "fields": fields}
    if isinstance(folder_date, str) and _ISO_RE.fullmatch(folder_date):
        body["reference_date"] = folder_date
    return body


def _credentials(env):
    account = (env.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = (env.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if not _ACCOUNT_RE.fullmatch(account) or not token or any(c in token for c in "\r\n"):
        raise MissingCredentials()
    return account, token


def _endpoint(account):
    return f"{API}/accounts/{account}{MODEL_PATH}"


def fetch_report_date(pages, folder_date, env=None, opener=None):
    import os
    env = os.environ if env is None else env
    account, token = _credentials(env)
    body = build_request(pages, folder_date)
    if not body["state"].strip():
        raise ClairicError("empty_state")
    raw = json.dumps(body).encode()
    request = urllib.request.Request(
        _endpoint(account),
        data=raw,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=TIMEOUT_S) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except TimeoutError as exc:
        raise ClairicError("timeout") from exc
    except urllib.error.HTTPError as exc:
        raise ClairicError(f"http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = "timeout" if isinstance(getattr(exc, "reason", None), TimeoutError) else "transport"
        raise ClairicError(reason) from exc
    except OSError as exc:
        raise ClairicError("transport") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ClairicError("bad_response")
    try:
        parsed = json.loads(payload.decode())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ClairicError("bad_response") from exc
    return _report_date(parsed)


def _report_date(payload):
    if not isinstance(payload, dict):
        raise ClairicError("bad_response")
    if payload.get("success") is False:
        raise ClairicError("api_error")
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise ClairicError("bad_response")
    answers = result.get("answers")
    if not isinstance(answers, dict):
        raise ClairicError("bad_response")
    report = answers.get("report_date") or {}
    if not isinstance(report, dict):
        raise ClairicError("bad_response")
    return report.get("value"), report.get("confidence")


def sanity_failure(iso, folder_date, today):
    """None when the date is not in the future and not more than 45 days before the folder date."""
    try:
        report = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return "no_printed_date"
    if report > today:
        return "future_date"
    try:
        folder = date.fromisoformat(folder_date) if isinstance(folder_date, str) else None
    except ValueError:
        folder = None
    if folder is None or (folder - report).days > STALE_DAYS:
        return "stale_vs_folder"
    return None


def _confidence_ok(confidence):
    try:
        return float(confidence) >= CONFIDENCE_MIN
    except (TypeError, ValueError):
        return False


def reject_clairic(value, confidence, folder_date, today):
    if not isinstance(value, str) or _ISO_RE.fullmatch(value) is None:
        return "no_printed_date"
    try:
        date.fromisoformat(value)
    except ValueError:
        return "no_printed_date"
    if not _confidence_ok(confidence):
        return "clairic_low_confidence"
    return sanity_failure(value, folder_date, today)


def _log(reason, date_flag=None, date_source=None):
    row = {"event": "clairic_date", "reason": reason}
    if date_flag:
        row["date_flag"] = date_flag
    if date_source:
        row["date_source"] = date_source
    print(json.dumps(row, separators=(",", ":")), file=sys.stderr)


def apply(found, page_text, folder_date, env=None):
    """Return (date tuple, date_flag or None). Flags off returns `found` unchanged and logs nothing."""
    import os
    env = os.environ if env is None else env
    if not _enabled("CLAIRIC_DATES_ENABLED", env) and not _enabled("CLAIRIC_DATE_SANITY_ENABLED", env):
        return found, None
    chosen = found
    flag = None
    transport = None
    clairic_answered = False
    if _enabled("CLAIRIC_DATES_ENABLED", env):
        try:
            value, confidence = fetch_report_date(page_text, folder_date, env=env)
        except MissingCredentials:
            transport = "missing_credentials"
        except ClairicError as exc:
            transport = exc.reason
        else:
            clairic_answered = True
            reject = reject_clairic(value, confidence, folder_date, today_et())
            if reject is None:
                _log("accepted", date_source="clairic")
                return (value, "clairic", None, None), None
            flag = reject
    # A successful Clairic call that was not accepted still sanity-checks the ladder.
    # A transport failure does not, unless the sanity flag is on by itself.
    check_ladder = _enabled("CLAIRIC_DATE_SANITY_ENABLED", env) or clairic_answered
    if check_ladder and chosen[0]:
        failure = sanity_failure(chosen[0], folder_date, today_et())
        if failure in {"future_date", "stale_vs_folder"} and folder_date:
            chosen = (folder_date, "folder", None, None)
            flag = failure
    if transport or flag:
        _log(transport or flag, date_flag=flag, date_source=chosen[1])
    return chosen, flag
