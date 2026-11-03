#!/usr/bin/env python3
"""Weekly live-setup drift check: compare the repo with what is actually live.

Read-only everywhere, stdlib only, no model calls. Three checks:

1. tailnet_policy: the live Tailscale ACL (GET /api/v2/tailnet/-/acl) against
   cloud/tailscale/policy.hujson, compared semantically.
2. tailnet_trust: whether the app host's radon-api env sets
   RADON_TAILNET_TRUST_MODE, and whether it is in log or enforcing mode. The
   remote command prints fixed tokens only; the value never leaves the host.
3. github: rulesets, main branch protection, deploy keys (id/title/read_only
   only) and secret scanning for both repos, against
   docs/security-live-baseline.json.

Output: a private Markdown report (docs/security-report-template.md rules)
and one public counts-only line. Exit 0 all ok, 1 drift, 75 operator
required or incomplete, 2 usage. Runbook: docs/operations.md
"Weekly live drift check".
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ALL_CHECKS = ("tailnet_policy", "tailnet_trust", "github")
OPERATOR_PLACEHOLDER = "__OPERATOR_LOGIN__"
BASELINE_PATH = "docs/security-live-baseline.json"
TAILSCALE_API = "https://api.tailscale.com/api/v2"
GITHUB_SCOPE = "admin read: a fine-grained token with Administration: read and Metadata: read, or the repo admin's gh login"
EX_DRIFT, EX_INCOMPLETE = 1, 75

# Runs as root on the app host. It prints only the fixed tokens below: no
# matched line, no value, no file content. The container runtime hands the
# API /etc/radon/env through --env-file (cloud/scripts/radon-app-runtime.sh),
# so that file decides the mode; the unit Environment= line is reported for
# completeness. The last assignment wins, as with docker --env-file.
TRUST_PROBE_COMMAND = (
    "f=/etc/radon/env; "
    "if [ ! -r \"$f\" ]; then echo file=UNREADABLE; "
    "elif grep -Eq '^RADON_TAILNET_TRUST_MODE=' \"$f\"; then echo file=PRESENT; "
    "if grep -E '^RADON_TAILNET_TRUST_MODE=' \"$f\" | tail -n 1 | "
    "grep -Eiq \"^RADON_TAILNET_TRUST_MODE=[\\\"']?(log)?[\\\"']?[[:space:]]*\\$\"; "
    "then echo mode=LOG; else echo mode=ENFORCING; fi; "
    "else echo file=ABSENT; fi; "
    "if grep -Eq \"^RADON_TRUSTED_TAILNET_PEERS=[\\\"']?[0-9]\" \"$f\" 2>/dev/null; "
    "then echo peers=PRESENT; else echo peers=ABSENT; fi; "
    "if systemctl show -p Environment radon-api.service 2>/dev/null | grep -q 'RADON_TAILNET_TRUST_MODE='; "
    "then echo unit=PRESENT; else echo unit=ABSENT; fi"
)
_PROBE_TOKENS = {
    "file": {"PRESENT", "ABSENT", "UNREADABLE"},
    "mode": {"LOG", "ENFORCING"},
    "unit": {"PRESENT", "ABSENT"},
    "peers": {"PRESENT", "ABSENT"},
}


class AuthMissing(Exception):
    """A required credential is absent or unsafe; never guess past it."""


class HttpError(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class GhError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"HTTP {status} {message}".strip())
        self.status = status
        self.message = message


# ------------------------------------------------------------------ HuJSON --

def strip_hujson(text: str) -> str:
    """HuJSON -> JSON: drop // and /* */ comments and trailing commas."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
        elif ch == '"':
            in_str = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated block comment")
            i = end + 2
        elif ch == ",":
            j = i + 1
            while j < n:
                if text[j].isspace():
                    j += 1
                elif text.startswith("//", j):
                    while j < n and text[j] != "\n":
                        j += 1
                elif text.startswith("/*", j):
                    end = text.find("*/", j + 2)
                    j = n if end < 0 else end + 2
                else:
                    break
            if j < n and text[j] in "}]":
                i += 1
                continue
            out.append(ch)
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_hujson(text: str):
    return json.loads(strip_hujson(text))


def _canon(value):
    """Order-insensitive canonical form: lists are sets of canonical items."""
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return sorted((_canon(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def _key(value) -> str:
    return json.dumps(_canon(value), sort_keys=True)


def _substitute(value, placeholder: str, replacement: str):
    if isinstance(value, dict):
        return {k: _substitute(v, placeholder, replacement) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, placeholder, replacement) for v in value]
    if value == placeholder:
        return replacement
    return value


# ---------------------------------------------------------------- fetchers --

def _read_private_file(path_text: str, name: str) -> str:
    path = Path(path_text)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise AuthMissing(f"{name} file unreadable") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise AuthMissing(f"{name} file must be a regular 0600 file owned by this account")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            return stream.read()
    finally:
        if fd >= 0:
            os.close(fd)


class LiveFetchers:
    """Read-only network and ssh access. GET only; nothing is written."""

    def __init__(self, env=None, app_host: str = "radon-app", gh_binary: str | None = None):
        self.env = dict(os.environ if env is None else env)
        self.app_host = app_host
        self.gh_binary = gh_binary or shutil.which("gh", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin") or "gh"

    def tailscale_token(self) -> str:
        token = self.env.get("TAILSCALE_API_TOKEN", "").strip()
        if token:
            return token
        if self.env.get("TAILSCALE_API_TOKEN_FILE"):
            token = _read_private_file(self.env["TAILSCALE_API_TOKEN_FILE"], "TAILSCALE_API_TOKEN_FILE").strip()
            if not token:
                raise AuthMissing("TAILSCALE_API_TOKEN_FILE is empty")
            return token
        if self.env.get("TAILSCALE_OAUTH_CLIENT_FILE"):
            return self._oauth_token(_read_private_file(self.env["TAILSCALE_OAUTH_CLIENT_FILE"], "TAILSCALE_OAUTH_CLIENT_FILE"))
        raise AuthMissing("TAILSCALE_API_TOKEN, TAILSCALE_API_TOKEN_FILE or TAILSCALE_OAUTH_CLIENT_FILE")

    def _oauth_token(self, text: str) -> str:
        fields = {}
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields[key.strip()] = value.strip().strip("'\"")
        if not fields.get("client_id") or not fields.get("client_secret"):
            raise AuthMissing("TAILSCALE_OAUTH_CLIENT_FILE needs client_id= and client_secret=")
        body = urllib.parse.urlencode({"client_id": fields["client_id"], "client_secret": fields["client_secret"],
                                       "grant_type": "client_credentials"}).encode()
        request = urllib.request.Request(f"{TAILSCALE_API}/oauth/token", data=body, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                token = json.loads(response.read()).get("access_token", "")
        except urllib.error.HTTPError as exc:
            raise HttpError(exc.code) from None
        if not token:
            raise AuthMissing("Tailscale OAuth exchange returned no token")
        return token

    def tailscale_acl(self) -> str:
        token = self.tailscale_token()
        request = urllib.request.Request(f"{TAILSCALE_API}/tailnet/-/acl", method="GET",
                                         headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise HttpError(exc.code) from None

    def trust_probe(self):
        argv = ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
                f"root@{self.app_host}", TRUST_PROBE_COMMAND]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return 255, ""
        return done.returncode, done.stdout

    def gh(self, path: str):
        env = {k: v for k, v in self.env.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
        token_file = self.env.get("RADON_LIVE_DRIFT_GH_TOKEN_FILE")
        if token_file and Path(token_file).exists():
            env["GH_TOKEN"] = _read_private_file(token_file, "RADON_LIVE_DRIFT_GH_TOKEN_FILE").strip()
        elif self.env.get("GH_TOKEN"):
            env["GH_TOKEN"] = self.env["GH_TOKEN"]
        done = subprocess.run([self.gh_binary, "api", "-H", "Accept: application/vnd.github+json", path],
                              capture_output=True, text=True, timeout=60, env=env)
        if done.returncode != 0:
            match = re.search(r"gh: (.*?) \(HTTP (\d{3})\)", done.stderr or "")
            if match:
                raise GhError(int(match.group(2)), match.group(1)[:120])
            raise GhError(0, "gh api failed")
        return json.loads(done.stdout)


def fixture_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", path)


class FixtureFetchers:
    """Offline fixture: tailscale_acl.json|.hujson|.error, trust_probe.txt, gh/<name>.json|.error."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def tailscale_acl(self) -> str:
        error = self.directory / "tailscale_acl.error"
        if error.exists():
            raise HttpError(int(error.read_text().split()[0]))
        for name in ("tailscale_acl.json", "tailscale_acl.hujson"):
            if (self.directory / name).exists():
                return (self.directory / name).read_text()
        raise AuthMissing("no tailscale_acl fixture")

    def trust_probe(self):
        path = self.directory / "trust_probe.txt"
        if not path.exists():
            return 255, ""
        rc, _, stdout = path.read_text().partition("\n")
        return int(rc), stdout

    def gh(self, path: str):
        base = self.directory / "gh" / fixture_name(path)
        error, payload = Path(f"{base}.error"), Path(f"{base}.json")
        if error.exists():
            status, _, message = error.read_text().partition(" ")
            raise GhError(int(status), message.strip())
        if payload.exists():
            return json.loads(payload.read_text())
        raise GhError(404, "Not Found")


# ------------------------------------------------------------------ checks --

def _row(check, status, counts=None, detail=None, action=None):
    return {"check": check, "status": status, "counts": counts or {}, "detail_private": list(detail or []),
            "action": action}


def check_tailnet_policy(repo_root: Path, baseline: dict, fetchers) -> dict:
    cfg = baseline["tailnet_policy"]
    name = "tailnet_policy"
    try:
        repo = parse_hujson((repo_root / cfg["path"]).read_text())
    except (OSError, ValueError):
        return _row(name, "error", detail=[f"repo policy `{cfg['path']}` unreadable or not HuJSON"])
    try:
        live = parse_hujson(fetchers.tailscale_acl())
    except AuthMissing as exc:
        return _row(name, "operator_required", detail=[f"Tailscale credential missing: {exc}"],
                    action="Provision a Tailscale OAuth client with only the `policy_file:read` scope and store it "
                           "0600 as `TAILSCALE_OAUTH_CLIENT_FILE` (docs/operations.md \"Weekly live drift check\").")
    except HttpError as exc:
        if exc.status in (401, 403):
            return _row(name, "operator_required",
                        detail=[f"Tailscale API refused the credential (HTTP {exc.status}); it needs policy_file:read"],
                        action="Replace the Tailscale credential with one that holds `policy_file:read`.")
        return _row(name, "error", detail=[f"Tailscale API HTTP {exc.status}"])
    except (OSError, ValueError) as exc:
        return _row(name, "error", detail=[f"Tailscale API unreachable or unparseable ({type(exc).__name__})"])
    if not isinstance(live, dict) or not isinstance(repo, dict):
        return _row(name, "error", detail=["policy is not a JSON object"])

    detail = []
    group = live.get("groups", {}).get(cfg["placeholder_group"]) if isinstance(live.get("groups"), dict) else None
    if isinstance(group, list) and len(group) == 1 and isinstance(group[0], str):
        repo = _substitute(repo, cfg["placeholder"], group[0])
        detail.append(f"placeholder `{cfg['placeholder']}` bound from live `{cfg['placeholder_group']}`")
    else:
        detail.append(f"placeholder `{cfg['placeholder']}` not bound: live `{cfg['placeholder_group']}` is not one login")

    only_repo = only_live = differ = 0
    for section in sorted(set(repo) | set(live)):
        a, b = repo.get(section), live.get(section)
        if _key(a) == _key(b):
            continue
        differ += 1
        if section not in live:
            detail.append(f"`{section}`: present in repo, absent live")
            only_repo += len(a) if isinstance(a, (list, dict)) else 1
            continue
        if section not in repo:
            detail.append(f"`{section}`: present live, absent in repo")
            only_live += len(b) if isinstance(b, (list, dict)) else 1
            continue
        if isinstance(a, list) and isinstance(b, list):
            ca, cb = Counter(_key(v) for v in a), Counter(_key(v) for v in b)
            r, l = sum((ca - cb).values()), sum((cb - ca).values())
            only_repo, only_live = only_repo + r, only_live + l
            detail.append(f"`{section}`: {r} entries only in repo, {l} only live")
        elif isinstance(a, dict) and isinstance(b, dict):
            missing = sorted(set(a) - set(b))
            extra = sorted(set(b) - set(a))
            changed = sorted(k for k in set(a) & set(b) if _key(a[k]) != _key(b[k]))
            only_repo += len(missing) + len(changed)
            only_live += len(extra) + len(changed)
            detail.append(f"`{section}`: keys only in repo {missing}, only live {extra}, changed {changed}")
        else:
            only_repo, only_live = only_repo + 1, only_live + 1
            detail.append(f"`{section}`: value differs")
    counts = {"sections_differ": differ, "only_repo": only_repo, "only_live": only_live}
    if differ:
        return _row(name, "drift", counts, detail,
                    action="Reconcile the live tailnet policy with `cloud/tailscale/policy.hujson` "
                           "(docs/operations.md \"Tailnet policy\"), or commit the reviewed live change.")
    return _row(name, "ok", counts, detail)


def check_tailnet_trust(baseline: dict, fetchers) -> dict:
    name = "tailnet_trust"
    cfg = baseline["tailnet_trust"]
    rc, stdout = fetchers.trust_probe()
    if rc == 255:
        return _row(name, "operator_required", detail=["ssh to the app host failed (BatchMode, exit 255)"],
                    action="Confirm `ssh -o BatchMode=yes root@radon-app true` works from the account running this check.")
    if rc != 0:
        return _row(name, "error", detail=[f"remote probe exit {rc}"])
    seen: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or key not in _PROBE_TOKENS or value not in _PROBE_TOKENS[key] or key in seen:
            return _row(name, "error", detail=["remote probe printed an unexpected line (not echoed)"])
        seen[key] = value
    if not {"file", "unit", "peers"} <= set(seen) or (seen["file"] == "PRESENT") != ("mode" in seen):
        return _row(name, "error", detail=["remote probe output incomplete"])
    if seen["file"] == "UNREADABLE":
        return _row(name, "error", detail=[f"`{cfg['env_file']}` unreadable on the app host"])
    effective = "enforce" if seen.get("mode") == "ENFORCING" else "log"
    counts = {"env_file_set": int(seen["file"] == "PRESENT"), "unit_env_set": int(seen["unit"] == "PRESENT"),
              "peers_set": int(seen["peers"] == "PRESENT"), "enforcing": int(effective == "enforce")}
    detail = [
        f"`RADON_TAILNET_TRUST_MODE` in `{cfg['env_file']}`: {seen['file']}"
        + (f" ({seen['mode']})" if "mode" in seen else ""),
        f"`RADON_TAILNET_TRUST_MODE` in `{cfg['service']}` Environment=: {seen['unit']}"
        " (the container runtime forwards only the env file)",
        f"`RADON_TRUSTED_TAILNET_PEERS` set: {seen['peers']}"
        + ("" if seen["peers"] == "PRESENT" else " (enforce with an empty list is loopback-only)"),
        f"effective mode: {effective}; expected: {cfg['expected_mode']}",
    ]
    if effective != cfg["expected_mode"]:
        return _row(name, "drift", counts, detail,
                    action="Set `RADON_TAILNET_TRUST_MODE=enforce` in `/etc/radon/env` on the app host per "
                           "docs/operations.md \"Tailnet trust narrowing\" (unlisted tailnet peers keep the JWT bypass until then).")
    return _row(name, "ok", counts, detail)


def _scope_row(name, repo, why):
    return _row(name, "operator_required", detail=[f"{why}; needs {GITHUB_SCOPE}"],
                action=f"Give the check admin read on `{repo}`: a fine-grained token with Administration: read and "
                       "Metadata: read in `RADON_LIVE_DRIFT_GH_TOKEN_FILE`, or run it as the repo admin.")


def _normalize_ruleset(row: dict) -> dict:
    keep = ("id", "name", "target", "enforcement", "conditions", "rules", "bypass_actors")
    return {k: row.get(k) for k in keep}


def _protection(payload) -> dict:
    checks = payload.get("required_status_checks") or {}
    flag = lambda key: (payload.get(key) or {}).get("enabled")
    return {
        "protected": True,
        "strict": checks.get("strict"),
        "enforce_admins": flag("enforce_admins"),
        "allow_force_pushes": flag("allow_force_pushes"),
        "allow_deletions": flag("allow_deletions"),
        "required_checks": sorted([c.get("context"), c.get("app_id")] for c in checks.get("checks") or []),
    }


def _gh_sub(name, repo, call):
    """Run one GitHub read; map auth failures to operator_required rows."""
    try:
        return call(), None
    except GhError as exc:
        if exc.status in (401, 403, 404):
            return None, _scope_row(name, repo, f"GitHub HTTP {exc.status}")
        return None, _row(name, "error", detail=[f"GitHub read failed (HTTP {exc.status or 'n/a'})"])
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return None, _row(name, "error", detail=[f"GitHub read failed ({type(exc).__name__})"])


def check_github_repo(repo: str, expected: dict, fetchers) -> list[dict]:
    names = {sub: f"github:{repo}:{sub}" for sub in ("rulesets", "branch_protection", "deploy_keys", "secret_scanning")}
    meta, failed = _gh_sub(names["rulesets"], repo, lambda: fetchers.gh(f"repos/{repo}"))
    if failed is not None:
        return [{**failed, "check": n} for n in names.values()]
    if not isinstance(meta, dict) or (meta.get("permissions") or {}).get("admin") is not True:
        return [_scope_row(n, repo, "the GitHub identity is not admin on the repo") for n in names.values()]
    drift_action = lambda sub: (f"Reconcile live {sub.replace('_', ' ')} on `{repo}` with `{BASELINE_PATH}`, "
                                "or record the reviewed change in that file.")
    rows = []

    # Rulesets
    name = names["rulesets"]
    listing, failed = _gh_sub(name, repo, lambda: fetchers.gh(f"repos/{repo}/rulesets"))
    if failed is None:
        live, failed_detail = {}, None
        for item in listing or []:
            got, failed_detail = _gh_sub(name, repo, lambda i=item: fetchers.gh(f"repos/{repo}/rulesets/{i['id']}"))
            if failed_detail is not None:
                break
            live[got["id"]] = _normalize_ruleset(got)
        if failed_detail is not None:
            rows.append(failed_detail)
        else:
            want = {r["id"]: _normalize_ruleset(r) for r in expected["rulesets"]}
            detail, changed = [], 0
            for rid in sorted(set(want) - set(live)):
                detail.append(f"ruleset `{rid}` `{want[rid]['name']}` expected, absent live")
            for rid in sorted(set(live) - set(want)):
                detail.append(f"ruleset `{rid}` `{live[rid]['name']}` live, not in baseline")
            for rid in sorted(set(want) & set(live)):
                fields = [k for k in want[rid] if _key(want[rid][k]) != _key(live[rid][k])]
                if fields:
                    changed += 1
                    detail.append(f"ruleset `{rid}` `{want[rid]['name']}`: {', '.join(fields)} differ")
            counts = {"expected": len(want), "live": len(live), "missing": len(set(want) - set(live)),
                      "unexpected": len(set(live) - set(want)), "changed": changed}
            drifted = counts["missing"] or counts["unexpected"] or changed
            rows.append(_row(name, "drift" if drifted else "ok", counts, detail, drift_action("rulesets") if drifted else None))
    else:
        rows.append(failed)

    # Branch protection on main
    name = names["branch_protection"]
    want = expected["branch_protection"]
    def read_protection():
        try:
            return _protection(fetchers.gh(f"repos/{repo}/branches/main/protection"))
        except GhError as exc:
            if exc.status == 404 and "not protected" in exc.message.lower():
                return {"protected": False}
            raise

    live, failed = _gh_sub(name, repo, read_protection)
    if failed is not None:
        rows.append(failed)
    else:
        detail = []
        fields = [k for k in want if k != "required_checks" and want[k] != live.get(k)]
        for field in fields:
            detail.append(f"`{field}`: expected {json.dumps(want[field])}, live {json.dumps(live.get(field))}")
        missing = unexpected = 0
        if want.get("protected"):
            a = {tuple(c) for c in want["required_checks"]}
            b = {tuple(c) for c in live.get("required_checks", [])}
            missing, unexpected = len(a - b), len(b - a)
            for context, app in sorted(a - b, key=str):
                detail.append(f"required check `{context}` (app {app}) missing live")
            for context, app in sorted(b - a, key=str):
                detail.append(f"required check `{context}` (app {app}) live, not in baseline")
            detail.append(f"{len(b)} required checks live, {len(a)} expected")
        counts = {"fields_differ": len(fields), "missing_checks": missing, "unexpected_checks": unexpected}
        drifted = fields or missing or unexpected
        rows.append(_row(name, "drift" if drifted else "ok", counts, detail,
                         drift_action("branch_protection") if drifted else None))

    # Deploy keys: id, title and read_only only; key material is never kept.
    name = names["deploy_keys"]
    keys, failed = _gh_sub(name, repo, lambda: fetchers.gh(f"repos/{repo}/keys?per_page=100"))
    if failed is None:
        live_keys = [{"id": k.get("id"), "title": k.get("title"), "read_only": k.get("read_only")} for k in keys or []]
        want_c = Counter((k["title"], k["read_only"]) for k in expected["deploy_keys"])
        live_c = Counter((k["title"], k["read_only"]) for k in live_keys)
        missing, extra = want_c - live_c, live_c - want_c
        detail = []
        for (title, ro), count in sorted(missing.items(), key=str):
            detail.append(f"expected deploy key `{title}` ({'read-only' if ro else 'write'}) x{count} absent live")
        for k in live_keys:
            if extra[(k["title"], k["read_only"])] > 0:
                extra[(k["title"], k["read_only"])] -= 1
                detail.append(f"unexpected deploy key id `{k['id']}` `{k['title']}` "
                              f"({'read-only' if k['read_only'] else 'write'}); retire with "
                              f"`gh repo deploy-key delete {k['id']} -R {repo}` if no longer used")
        counts = {"expected": sum(want_c.values()), "live": len(live_keys), "missing": sum(missing.values()),
                  "unexpected": sum((live_c - want_c).values())}
        drifted = counts["missing"] or counts["unexpected"]
        rows.append(_row(name, "drift" if drifted else "ok", counts, detail, drift_action("deploy_keys") if drifted else None))
    else:
        rows.append(failed)

    # Secret scanning and push protection. Admin is proven above, so an absent
    # field means the repo has no such settings (a private repo without
    # Advanced Security), not a missing scope.
    name = names["secret_scanning"]
    sa = meta.get("security_and_analysis")
    want_sa = expected["security_and_analysis"]
    if sa is None:
        live_sa = None
    elif want_sa is None:
        live_sa = {k: v.get("status") for k, v in sa.items() if isinstance(v, dict)}
    else:
        live_sa = {k: (sa.get(k) or {}).get("status") for k in want_sa}
    detail = [f"expected {json.dumps(want_sa, sort_keys=True)}, live {json.dumps(live_sa, sort_keys=True)}"]
    a, b = want_sa or {}, live_sa or {}
    differ = sum(1 for k in set(a) | set(b) if a.get(k) != b.get(k)) or int(want_sa != live_sa)
    rows.append(_row(name, "drift" if differ else "ok", {"settings_differ": differ},
                     detail, drift_action("secret_scanning") if differ else None))
    return rows


def run_checks(repo_root: Path, baseline: dict, fetchers, checks=ALL_CHECKS) -> list[dict]:
    rows = []
    if "tailnet_policy" in checks:
        rows.append(check_tailnet_policy(repo_root, baseline, fetchers))
    if "tailnet_trust" in checks:
        rows.append(check_tailnet_trust(baseline, fetchers))
    if "github" in checks:
        for repo, expected in baseline["github"].items():
            rows.extend(check_github_repo(repo, expected, fetchers))
    return rows


# ------------------------------------------------------------------ output --

def _tally(rows) -> dict:
    tally = {"ok": 0, "drift": 0, "operator_required": 0, "error": 0}
    for row in rows:
        tally[row["status"]] += 1
    return tally


def public_line(rows, ts: str) -> str:
    t = _tally(rows)
    return f"radon-live-drift ts={ts} ok={t['ok']} drift={t['drift']} operator_required={t['operator_required']} error={t['error']}"


def exit_code(rows) -> int:
    t = _tally(rows)
    if t["drift"]:
        return EX_DRIFT
    if t["operator_required"] or t["error"]:
        return EX_INCOMPLETE
    return 0


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_report(rows, ts: str, checks) -> str:
    t = _tally(rows)
    status = "INCOMPLETE" if t["error"] else "OPERATOR_REQUIRED" if t["operator_required"] else "COMPLETE"
    limits = [f"`{c}` (not selected)" for c in ALL_CHECKS if c not in checks]
    limits += [f"`{r['check']}` ({r['status']})" for r in rows if r["status"] in ("operator_required", "error")]
    actions = [r for r in rows if r.get("action")]
    lines = [
        f"# radon-live-drift weekly {ts[:10]}", "", "## Summary", "",
        "| Field | Value |", "|---|---|",
        f"| Status | {status} |",
        f"| Generated | `{ts}` |",
        f"| Checks | {len(rows)} run: {t['ok']} ok, {t['drift']} drift, {t['operator_required']} operator_required, {t['error']} error |",
        f"| Drift | {t['drift']} |",
        f"| Coverage limits | {_cell(', '.join(limits)) if limits else 'none'} |",
        f"| Operator actions | {len(actions)} (see below) |",
        "", "## Operator actions", "",
    ]
    if actions:
        lines += [f"{i}. **`{r['check']}`**: {r['action']}" for i, r in enumerate(actions, 1)]
    else:
        lines.append("None.")
    lines += ["", "## Checks", "", "| Check | Status | Counts |", "|---|---|---|"]
    for r in rows:
        counts = ", ".join(f"{k} {v}" for k, v in r["counts"].items()) or "none"
        lines.append(f"| `{_cell(r['check'])}` | {r['status']} | {_cell(counts)} |")
    lines += ["", "## Findings", ""]
    for r in rows:
        lines += [f"### `{r['check']}`", ""]
        lines += [f"- {d}" for d in r["detail_private"]] or ["- no detail"]
        lines.append("")
    lines += ["## Verification", "", "```", public_line(rows, ts), "```", ""]
    return "\n".join(lines)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


def _parse_checks(text: str):
    checks = tuple(c.strip() for c in text.split(",") if c.strip())
    bad = [c for c in checks if c not in ALL_CHECKS]
    if bad or not checks:
        raise argparse.ArgumentTypeError(f"unknown checks: {', '.join(bad) or '(none)'}; choose from {', '.join(ALL_CHECKS)}")
    return checks


def main(argv=None, fetchers=None) -> int:
    parser = argparse.ArgumentParser(description="Weekly read-only live-setup drift check.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, help="private Markdown report path (written 0600)")
    parser.add_argument("--out-dir", type=Path, help="directory for live-drift-<date>.md (created 0700)")
    parser.add_argument("--json", action="store_true", help="print the private rows as JSON on stdout (the public line goes to stderr)")
    parser.add_argument("--checks", type=_parse_checks, default=ALL_CHECKS, help="comma list of " + ",".join(ALL_CHECKS))
    parser.add_argument("--offline-fixture", type=Path, help="read every live input from this fixture directory")
    parser.add_argument("--app-host", default="radon-app")
    args = parser.parse_args(argv)

    try:
        baseline = json.loads((args.repo_root / BASELINE_PATH).read_text())
    except (OSError, ValueError):
        print(f"security_live_drift: cannot read {BASELINE_PATH} under --repo-root", file=sys.stderr)
        return 2
    if fetchers is None:
        fetchers = FixtureFetchers(args.offline_fixture) if args.offline_fixture else LiveFetchers(app_host=args.app_host)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = run_checks(args.repo_root, baseline, fetchers, args.checks)
    report = render_report(rows, ts, args.checks)
    if args.out:
        _write_private(args.out, report)
    if args.out_dir:
        args.out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(args.out_dir / f"live-drift-{ts[:10]}.md", report)
    if args.json:
        # stdout stays one JSON document; the counts line moves to stderr.
        print(json.dumps({"ts": ts, "rows": rows}, indent=2))
        print(public_line(rows, ts), file=sys.stderr)
    else:
        print(public_line(rows, ts))
    return exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())
