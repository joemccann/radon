#!/usr/bin/env python3
"""Render a Radon security-audit HTML report from the security-audit workflow's JSON output.

Usage:
    python3 scripts/security/gen_security_report.py <input.json> <output.html> [--date YYYY-MM-DD] [--fixes fixes.json]

<input.json> may be either:
  - the raw workflow result object ({confirmed, false_positives, clean_areas, counts, ...}), or
  - a task-output wrapper that nests it under a top-level "result" key (the file the
    Workflow tool writes to the task output path; "result" may itself be a JSON string).

--fixes fixes.json (optional): a list of {id,sev,title,finding,fix,files[],tests} objects
documenting the patches you shipped for this audit. Omit it for a findings-only report.

The reusable audit workflow lives at .claude/workflows/security-audit.mjs.
Methodology + dimension catalog + audit log: docs/security-audit-playbook.md.
"""
import json, sys, html, argparse, re

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "false-positive": 5}
SEV_COLOR = {"critical": "#b3001b", "high": "#d7263d", "medium": "#e08a00",
             "low": "#3a7ca5", "info": "#5a6570", "false-positive": "#5a6570"}

# ── secret redaction ──────────────────────────────────────────────────────
#
# An audit report must never embed the literal credential it is reporting.
# 2026-07-18 did exactly that: a finding quoted the live TWS value, the rendered
# HTML was committed to this PUBLIC repo, and the literal is now permanently in
# git history. The secret scan caught it, it was allowlisted as "already public,
# not a new exposure", and a later rebase renumbered that commit, orphaned the
# allowlist and silently skipped the deploy for hours.
#
# Redaction lives inside esc() on purpose. Every rendered field flows through
# it, so an audit agent cannot defeat this by inventing a new field or a
# differently-shaped finding -- the value never reaches the HTML in the first
# place. The variable NAME and surrounding prose survive so the finding stays
# actionable; only the value is replaced.

REDACTION_MARKER = "[REDACTED]"

# Names whose assigned value is a secret by construction. TWS_USERID is spelled
# out because it carries none of the giveaway words.
_SECRET_NAME = (
    r"(?:TWS_USERID|TWS_USER|"
    r"[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|APIKEY|API_KEY|CREDENTIAL)[A-Z0-9_]*)"
)

# A value already redacted, a shell/env reference, or an angle-bracket
# placeholder is left alone -- re-redacting it would only add noise.
_PLACEHOLDER = re.compile(r"^(?:<.*>|\*+|\$\{?\w+\}?|x{3,}|redacted|REDACTED)$", re.I)

_ASSIGNMENT = re.compile(rf"\b({_SECRET_NAME})(\s*=\s*)(['\"]?)([^\s'\"]{{4,}})\3")

# "password example: <value>" / "credentials like <value>" -- the shape an audit
# narrative uses when it illustrates a finding.
_PROSE = re.compile(
    r"(?i)\b((?:credential|password)s?\s+(?:example|like)\s*[:=]?\s*)(['\"]?)(\S{4,}?)\2(?=\s|$|[.,;])"
)


def _redact_assignment(m):
    if _PLACEHOLDER.match(m.group(4)):
        return m.group(0)
    return f"{m.group(1)}{m.group(2)}{REDACTION_MARKER}"


def _redact_prose(m):
    if _PLACEHOLDER.match(m.group(3)):
        return m.group(0)
    return f"{m.group(1)}{REDACTION_MARKER}"


def redact_secrets(text):
    """Replace credential literals with a marker, keeping the name and context."""
    text = _ASSIGNMENT.sub(_redact_assignment, text)
    return _PROSE.sub(_redact_prose, text)


def esc(s):
    """Escape for HTML AND strip any credential literal.

    Redaction runs on the raw string before escaping, so a value cannot slip
    through by being entity-encoded first.
    """
    return html.escape(redact_secrets(str(s if s is not None else "")))


def load_result(path):
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "result" in data and "confirmed" not in data:
        res = data["result"]
        return json.loads(res) if isinstance(res, str) else res
    return data


def rsev(f):
    v = f.get("verdict") or {}
    return v.get("revised_severity") or f.get("severity") or "info"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--date", default="")
    ap.add_argument("--fixes", default="")
    a = ap.parse_args()

    res = load_result(a.input)
    conf = res.get("confirmed", [])
    fps = res.get("false_positives", [])
    clean = res.get("clean_areas", [])
    counts = res.get("counts", {})
    conf.sort(key=lambda f: (SEV_ORDER.get(rsev(f), 9), 0 if (f.get("verdict") or {}).get("exploitable") else 1))

    fixes = []
    if a.fixes:
        with open(a.fixes) as f:
            fixes = json.load(f)

    n_expl = sum(1 for f in conf if (f.get("verdict") or {}).get("exploitable"))
    sev_counts = {}
    for f in conf:
        sev_counts[rsev(f)] = sev_counts.get(rsev(f), 0) + 1

    rows = []
    for i, f in enumerate(conf, 1):
        v = f.get("verdict") or {}
        s = rsev(f)
        badge = ('<span class="expl yes">EXPLOITABLE</span>' if v.get("exploitable")
                 else '<span class="expl no">defense-in-depth</span>')
        rows.append(f'''<tr class="sev-{s}">
  <td class="num">{i}</td>
  <td><span class="sevtag" style="background:{SEV_COLOR.get(s,'#555')}">{esc(s).upper()}</span><br>{badge}</td>
  <td><div class="ftitle">{esc(f.get('title'))}</div><div class="meta">{esc(f.get('dimension'))} · {esc(f.get('category'))} · patch: {esc(f.get('patch_complexity'))} · claimed {esc(f.get('severity'))}</div></td>
  <td class="loc">{esc(f.get('file'))}:{esc(f.get('line'))}</td>
  <td class="desc">{esc(f.get('description'))}<div class="verdict"><b>Verifier:</b> {esc(v.get('justification'))}</div><div class="rec"><b>Recommendation:</b> {esc(f.get('recommendation'))}</div></td>
</tr>''')

    fix_html = ""
    if fixes:
        blocks = []
        for fx in fixes:
            blocks.append(f'''<div class="fix">
  <div class="fixhdr"><span class="sevtag" style="background:{SEV_COLOR.get(fx.get('sev','info'),'#555')}">{esc(fx.get('sev','')).upper()}</span> <span class="fixid">{esc(fx.get('id',''))}</span> <span class="fixtitle">{esc(fx.get('title'))}</span></div>
  <div class="fixrow"><b>Finding:</b> {esc(fx.get('finding'))}</div>
  <div class="fixrow"><b>Fix:</b> {esc(fx.get('fix'))}</div>
  <div class="fixrow"><b>Files:</b> <code>{esc(', '.join(fx.get('files', [])))}</code></div>
  <div class="fixrow ok"><b>Verification:</b> {esc(fx.get('tests'))}</div>
</div>''')
        fix_html = f"<h2>Fixes Implemented &amp; Verified</h2>{''.join(blocks)}"

    fp_html = "".join(f'<tr><td>{esc(x.get("title"))}</td><td class="loc">{esc(x.get("file"))}</td><td>{esc(x.get("dimension"))}</td><td>{esc(x.get("reason"))}</td></tr>' for x in fps)
    clean_html = "".join(f'<tr><td><b>{esc(c.get("dimension"))}</b></td><td>{esc(c.get("summary"))}</td></tr>' for c in clean)
    legend = ", ".join(f"{k}: {v}" for k, v in sorted(sev_counts.items(), key=lambda x: SEV_ORDER.get(x[0], 9)))

    doc = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radon Security Audit{(' — ' + a.date) if a.date else ''}</title>
<style>
:root{{--bg:#0a0f14;--panel:#111922;--panel2:#0d141b;--ink:#e2e8f0;--muted:#8b97a5;--accent:#05AD98;--line:#1e2a36}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:1140px;margin:0 auto;padding:32px 22px 80px}}
h1{{font-size:30px;margin:0 0 4px;letter-spacing:-.5px}}
h2{{font-size:20px;margin:38px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px;color:#fff}}
.sub{{color:var(--muted);font-size:14px;margin-bottom:22px}}
.cards{{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}}
.card{{flex:1;min-width:120px;background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:14px 16px}}
.card .n{{font-size:28px;font-weight:700;color:#fff}}
.card .l{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
table{{width:100%;border-collapse:collapse;background:var(--panel2);border:1px solid var(--line);border-radius:4px;overflow:hidden;font-size:13.5px}}
th,td{{text-align:left;padding:10px 12px;vertical-align:top;border-bottom:1px solid var(--line)}}
th{{background:#0c131a;color:var(--muted);text-transform:uppercase;font-size:11px;letter-spacing:.6px}}
td.num{{color:var(--muted);width:30px}} td.loc{{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#9fb3c8;white-space:nowrap;max-width:240px;overflow:hidden;text-overflow:ellipsis}}
.sevtag{{display:inline-block;color:#fff;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.4px}}
.expl{{display:inline-block;font-size:10px;margin-top:5px;padding:1px 6px;border-radius:3px}}
.expl.yes{{background:#3a0a10;color:#ff9aa6;border:1px solid #7a1622}}
.expl.no{{background:#10202a;color:#7fb0c9;border:1px solid #244a5e}}
.ftitle{{font-weight:600;color:#fff}} .meta{{color:var(--muted);font-size:11.5px;margin-top:3px}}
.desc{{color:#c2cdd8;font-size:12.5px;max-width:520px}}
.verdict{{margin-top:7px;color:#8fa3b3;font-size:12px;border-left:2px solid var(--line);padding-left:8px}}
.rec{{margin-top:6px;color:#a7d8cf;font-size:12px}}
.fix{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:4px;padding:14px 16px;margin:12px 0}}
.fixhdr{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.fixid{{color:var(--accent);font-weight:700;font-family:ui-monospace,monospace}} .fixtitle{{font-weight:600;color:#fff}}
.fixrow{{font-size:13px;margin:5px 0;color:#c2cdd8}} .fixrow.ok{{color:#a7d8cf}}
code{{background:#0c131a;padding:1px 5px;border-radius:3px;font-size:12px;color:#9fb3c8}}
.note{{background:#14110a;border:1px solid #3a2f12;color:#d8c69a;padding:12px 16px;border-radius:4px;font-size:13px;margin:14px 0}}
.legend{{color:var(--muted);font-size:12px;margin:8px 0 0}}
</style></head><body><div class="wrap">
<h1>Radon — Security Audit</h1>
<div class="sub">{esc(a.date)} · single-operator options/equities trading app (app.radon.run) · dimensions: {esc(', '.join(res.get('dimensions_run', [])))}</div>
<div class="note"><b>Methodology.</b> Multi-agent workflow (.claude/workflows/security-audit.mjs): each dimension is audited by a finder, every finding is handed to an independent <b>adversarial verifier</b> (refute-first) that re-reads the code, and a <b>completeness + regression critic</b> hunts for missed surface and checks that past fixes have not regressed. Severities are verifier-revised for this app's real architecture, not raw scanner output.</div>
<div class="cards">
  <div class="card"><div class="n">{esc(counts.get('raw','?'))}</div><div class="l">raw findings</div></div>
  <div class="card"><div class="n">{esc(counts.get('confirmed', len(conf)))}</div><div class="l">confirmed real</div></div>
  <div class="card"><div class="n">{n_expl}</div><div class="l">exploitable</div></div>
  <div class="card"><div class="n">{esc(counts.get('false_positives', len(fps)))}</div><div class="l">false positives</div></div>
  <div class="card"><div class="n">{len(fixes)}</div><div class="l">fixes shipped</div></div>
</div>
<div class="legend">Confirmed by revised severity: {esc(legend)}.</div>
{fix_html}
<h2>Confirmed Findings ({len(conf)})</h2>
<p class="sub">Sorted by verifier-revised severity, exploitable first. "EXPLOITABLE" = a concrete production attack path was established; "defense-in-depth" = real weakness, no concrete exploit.</p>
<table><thead><tr><th>#</th><th>Severity</th><th>Finding</th><th>Location</th><th>Detail &amp; verifier verdict</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>False Positives Filtered ({len(fps)})</h2>
<p class="sub">Reported by a finder, refuted by the adversarial verifier — NOT vulnerabilities.</p>
<table><thead><tr><th>Claim</th><th>Location</th><th>Dimension</th><th>Why dismissed</th></tr></thead><tbody>{fp_html}</tbody></table>
<h2>Areas Examined &amp; Found Sound</h2>
<table><thead><tr><th>Dimension</th><th>Summary</th></tr></thead><tbody>{clean_html}</tbody></table>
<div class="sub" style="margin-top:30px;border-top:1px solid var(--line);padding-top:14px">Generated by scripts/security/gen_security_report.py from the security-audit workflow output.</div>
</div></body></html>'''

    with open(a.output, "w") as f:
        f.write(doc)
    print(f"wrote {a.output} ({len(doc)} bytes) — {len(conf)} confirmed, {n_expl} exploitable, {len(fps)} FPs, {len(fixes)} fixes")


if __name__ == "__main__":
    main()
