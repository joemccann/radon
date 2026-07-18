#!/usr/bin/env node
'use strict';
const fs = require('fs');

const SRC = process.argv[2];
const OUT = process.argv[3];
const parsed = JSON.parse(fs.readFileSync(SRC, 'utf8'));
const data = parsed.result || parsed;
const F = data.final;

const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

const LOCKED = [
  { header: 'Hosting', choice: 'Same VPS (<prod-host>)', note: 'Co-located beta stack. MUST enforce systemd `MemoryMax`/`CPUQuota` on `radon-beta-*` and build off-box or outside 09:30-16:00 ET, or a beta `next build` can OOM/starve the prod relay+api. (See known issues.)' },
  { header: 'IB posture', choice: 'No IB auth — RADON_BETA_NO_IB_AUTH=1', note: 'Beta never logs into the Gateway, holds no 2FA lock, ships no IB password. No order/cancel risk, no client-ID contention. IB-dependent panels render mock/empty in beta.' },
  { header: 'Access', choice: 'Clerk SATELLITE domain (LOCKED)', note: 'beta.radon.run attaches as a Clerk SATELLITE of the prod custom-domain instance (CLERK_ISSUER=https://clerk.radon.run — confirmed live). Inherits the same keys, JWKS, Google OAuth client + single-email lockdown automatically. FastAPI allowlist is ALLOWED_USER_IDS=<operator-user-id> (single user, confirmed) and works UNCHANGED because issuer/JWKS are shared. Beta env adds: NEXT_PUBLIC_CLERK_IS_SATELLITE=true, NEXT_PUBLIC_CLERK_DOMAIN=beta.radon.run, NEXT_PUBLIC_CLERK_SIGN_IN_URL=https://app.radon.run/sign-in (+ sign-up). Keys stay identical to prod. /sign-up reachability is harmless — the Google OAuth client only admits the operator email.' },
  { header: 'Data seed', choice: 'Exact copy of production', note: 'Separate beta Turso DB seeded by a full prod dump → restore. NEVER point beta at the prod DB URL/token. Unscrubbed real account data lives in beta, so the Google-OAuth single-email gate (see Access) is the only thing protecting it — which is why the satellite-domain choice matters. Pick a re-snapshot cadence.' },
];

const rich = (s) => esc(s)
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\n/g, '<br>');

const lockedHtml = LOCKED.map(d => `<div class="lcard"><div class="lhdr">${esc(d.header)}</div><div class="lchoice">${rich(d.choice)}</div><div class="lnote">${rich(d.note)}</div></div>`).join('');

// Phase 0 — live VPS capture 2026-06-01T04:18Z (ssh radon@ib-gateway). Read-only, secrets redacted.
const PHASE0 = {
  corrections: [
    ['Two repos, not one', 'App code = `joemccann/radon.git` at `/home/radon/radon` (this local repo; deploy target). Ops/config = `joemccann/radon-cloud.git` at `/home/radon/radon-cloud` — holds `.env`, `caddy/Caddyfile` source, `services/*.service`, and `scripts/deploy.sh`. The workflow treated radon-cloud as the app. Beta must split the same way: app → `/home/radon/radon-beta` (branch `beta`); beta env + deploy live alongside radon-cloud.'],
    ['deploy.sh found + read', '`/home/radon/radon-cloud/scripts/deploy.sh` (11 KB). `RADON_DIR=/home/radon/radon`; `git reset --hard origin/main`; uses **npm** (ci→install fallback) for BOTH root and web (not bun on VPS); copies `.env`→`web/.env` for build-time NEXT_PUBLIC_*; env-preflight gate on REQUIRED_VARS; **health-gated with auto-rollback**; `SERVICES=(radon-nextjs radon-api radon-relay radon-monitor radon-newsfeed)`; restart via `sudo systemctl`. Beta needs a parameterized clone targeting `/home/radon/radon-beta` + beta units.'],
    ['Live Caddy = /etc/caddy/Caddyfile', 'System `caddy.service` (NOT docker), hand-edited 84-line `/etc/caddy/Caddyfile` — OUTSIDE any git checkout, so safe from `git reset`. The repo copy `radon-cloud/caddy/Caddyfile` has DRIFTED (uncommitted `M`). Uses `{$DOMAIN:app.radon.run}`, `reverse_proxy :3000`, `/ws*`→:8765, `/api/ib/*`→:8321, `/edge-health`→:8330, auto Lets Encrypt. Beta = append a `beta.radon.run {}` block → :3001 + `/api/ib/*`→:8322 (no `/ws` — beta has no relay).'],
    ['Clerk = prod custom-domain instance', '`CLERK_ISSUER=https://clerk.radon.run` (confirmed). Allowlist var is **`ALLOWED_USER_IDS=<operator-user-id>`** (single user) — NOT the guessed `CLERK_ALLOWLIST`. Satellite reuses identical keys/JWKS/issuer, so the allowlist works unchanged. No second Clerk instance, no second Google OAuth client.'],
    ['Single .env, copied at build', 'One file `/home/radon/radon-cloud/.env` drives everything; deploy copies it to `/home/radon/radon/web/.env` so NEXT_PUBLIC_* bake in. No `.env.ib-mode`, no standalone `web/.env` on disk. Beta needs its own `/home/radon/radon-cloud/.env.beta` consumed by beta units + the beta build.'],
    ['DNS on Vercel; beta points away', 'radon.run NS = `ns1/ns2.vercel-dns.com`. `beta.radon.run` currently resolves to Vercel anycast **216.150.16.193** (app + media already point at <prod-host>). ACTION: in Vercel DNS set `beta.radon.run A <prod-host>` and detach beta from any Vercel project, else Vercel keeps intercepting.'],
  ],
  confirmed: [
    ['Beta ports FREE', '`3001` (Next.js), `8322` (FastAPI), `8331` (health) all confirmed unbound via `ss`. Beta runs NO relay (no IB) so :8765 is untouched.'],
    ['Migrations are idempotent ExecStartPre', 'radon-api runs `scripts/db/migrate.py` as `ExecStartPre`. Beta api unit runs the same against the beta DB — schema bootstrap is free.'],
    ['IB = single live account', '`TWS_USERID=<redacted>`, `TRADING_MODE=live`, one Gateway (docker, :4001). Validates the no-IB-auth beta posture — there is no paper account to fall back on.'],
    ['DB url confirmed', '`TURSO_DB_URL=libsql://<prod-turso-host>`, `RADON_DB_NO_REPLICA=1` on every unit. Beta gets a separate `radon-beta-…` Turso DB + token; seed via full dump→restore (your “exact copy” choice).'],
  ],
  risks: [
    ['2 vCPU / 7.6 GB RAM / 0 B SWAP', 'BIGGEST confirmed hazard. A Next.js prod build peaks well above 1.5 GB; running beta `next build` on-box with NO swap can OOM-kill prod (relay/api). MITIGATION (pick ≥1): build beta OFF-box (CI runner or laptop) and rsync the `.next` artifact; AND/OR add a swapfile; AND/OR `MemoryMax=`/`CPUQuota=`/`Nice=` on beta units + gate on-box builds outside 09:30–16:00 ET. 55 GB disk free is fine.'],
    ['sudoers / polkit scope UNRESOLVED', 'Needs root. `sudo -n true` FAILS (no blanket NOPASSWD) yet deploy.sh’s `sudo systemctl`/`sudo rm` work → a command-scoped `/etc/sudoers.d/radon-deploy` exists but is unreadable as `radon`. OPERATOR ACTION: `sudo cat /etc/sudoers.d/radon-deploy /etc/polkit-1/rules.d/*radon* 2>/dev/null` then extend NOPASSWD (and any admin-panel polkit rule) to cover `radon-beta-*` units + beta replica paths, or every beta `sudo systemctl` call prompts for a password and the deploy hangs/fails.'],
  ],
};
const p0group = (rows, cls) => rows.map(([t, d]) => `<div class="p0card ${cls}"><div class="p0t">${rich(t)}</div><div class="p0d">${rich(d)}</div></div>`).join('');

const sevRank = { critical: 0, high: 1, medium: 2, low: 3 };
const issues = [...F.knownIssues].sort((a, b) => (sevRank[a.severity] - sevRank[b.severity]));
const sevCounts = issues.reduce((m, i) => (m[i.severity] = (m[i.severity] || 0) + 1, m), {});

const decisionBadge = (d) => {
  const map = { reuse: 'reuse', isolate: 'isolate', clone: 'clone', depends: 'depends' };
  return `<span class="badge b-${map[d] || 'depends'}">${esc(d)}</span>`;
};

const portRows = F.portMap.map(p => `<tr><td>${esc(p.service)}</td><td class="mono">${esc(p.prodPort)}</td><td class="mono beta">${esc(p.betaPort)}</td></tr>`).join('');

const reuseRows = F.reuseMatrix.map(r => `<tr><td>${esc(r.component || r.item)}</td><td>${decisionBadge(r.decision)}</td><td>${rich(r.detail || r.why || '')}</td></tr>`).join('');

const envRows = F.envDelta.map(e => `<tr><td class="mono">${esc(e.key)}</td><td class="mono dim">${esc(e.file)}</td><td class="mono">${esc(e.prodValue)}</td><td class="mono beta">${esc(e.betaValue)}</td><td>${rich(e.note)}</td></tr>`).join('');

const stepsHtml = F.orderedSteps.map((s, i) => {
  const lis = (s.steps || []).map(st => `<li>${rich(st)}</li>`).join('');
  return `<details class="phase" ${i < 2 ? 'open' : ''}>
    <summary><span class="pnum">${i}</span><span class="ptitle">${esc(s.phase)}</span></summary>
    <div class="phase-body">
      <p class="goal"><strong>Goal:</strong> ${rich(s.goal)}</p>
      <ol class="steps">${lis}</ol>
      <p class="verify"><strong>&#10003; Verify:</strong> ${rich(s.verification)}</p>
    </div>
  </details>`;
}).join('');

const issueRows = issues.map(it => `<tr class="sev-${it.severity}">
  <td><span class="sev sev-${it.severity}">${esc(it.severity)}</span></td>
  <td class="cat">${esc(it.category)}</td>
  <td>${rich(it.issue)}</td>
  <td class="fix">${rich(it.fix)}</td></tr>`).join('');

const decisionItems = F.openDecisions.map(d => `<li>${rich(d)}</li>`).join('');

const scrutinyBlock = (s, name) => {
  const rows = s.findings.map(f => `<tr class="sev-${f.severity}">
    <td><span class="sev sev-${f.severity}">${esc(f.severity)}</span></td>
    <td class="cat">${esc(f.category)}</td>
    <td>${rich(f.issue)}<div class="where">@ ${esc(f.where || '')}</div></td>
    <td class="fix">${rich(f.fix)}</td></tr>`).join('');
  return `<details class="phase">
    <summary><span class="ptitle">${esc(name)} &mdash; ${esc(s.passName || '')} (${s.findings.length} findings)</span></summary>
    <div class="phase-body"><p class="goal"><strong>Verdict:</strong> ${rich(s.verdict)}</p>
    <table class="grid"><thead><tr><th>Sev</th><th>Category</th><th>Issue</th><th>Fix</th></tr></thead><tbody>${rows}</tbody></table></div>
  </details>`;
};

const subsysBlock = data.subsystemMaps.map(m => {
  const ri = (m.reuseVsIsolate || []).map(r => `<li>${decisionBadge(r.decision)} <strong>${esc(r.item)}</strong> &mdash; ${rich(r.why)}</li>`).join('');
  const cs = (m.concreteSteps || []).map(s => `<li>${rich(s)}</li>`).join('');
  const rk = (m.risks || []).map(s => `<li>${rich(s)}</li>`).join('');
  const oq = (m.openQuestions || []).map(s => `<li>${rich(s)}</li>`).join('');
  return `<details class="phase"><summary><span class="ptitle">${esc(m.subsystem)}</span></summary>
    <div class="phase-body">
      <p><strong>Current state:</strong> ${rich(m.currentState)}</p>
      <p><strong>Beta approach:</strong> ${rich(m.betaApproach)}</p>
      <h4>Reuse vs isolate</h4><ul class="tight">${ri}</ul>
      <h4>Concrete steps</h4><ol class="steps">${cs}</ol>
      <h4>Risks</h4><ul class="tight risk">${rk}</ul>
      <h4>Open questions</h4><ul class="tight">${oq}</ul>
    </div></details>`;
}).join('');

const html = `<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>beta.radon.run &mdash; Staging Environment Plan</title>
<style>
:root{
  --bg:#0a0e14; --panel:#111722; --panel2:#0d131c; --border:#1e2a3a;
  --ink:#e6edf3; --dim:#8b9bb0; --faint:#5a6b80;
  --accent:#4da3ff; --beta:#c792ea; --ok:#3fb950;
  --crit:#ff5c5c; --high:#ff9f43; --med:#f5d76e; --low:#6aa9ff;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:48px 28px 120px}
header.top{border-bottom:1px solid var(--border);padding-bottom:24px;margin-bottom:36px}
.eyebrow{color:var(--beta);font-size:12px;letter-spacing:.18em;text-transform:uppercase;font-weight:600}
h1{font-size:34px;margin:8px 0 6px;letter-spacing:-.02em}
h1 .b{color:var(--beta)}
.sub{color:var(--dim);font-size:15px}
.meta{display:flex;gap:22px;flex-wrap:wrap;margin-top:18px;font-size:12.5px;color:var(--faint)}
.meta b{color:var(--dim);font-weight:600}
h2{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin:48px 0 16px;font-weight:700;border-left:3px solid var(--accent);padding-left:12px}
h4{margin:18px 0 6px;font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
p{margin:0 0 12px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:4px;padding:22px 24px;margin-bottom:14px}
.callout{border-left:3px solid var(--high);background:linear-gradient(90deg,rgba(255,159,67,.08),transparent);padding:16px 20px;border-radius:4px;margin-bottom:14px}
.callout.crit{border-color:var(--crit);background:linear-gradient(90deg,rgba(255,92,92,.10),transparent)}
.callout h3{margin:0 0 8px;font-size:14px;color:var(--high)}
.callout.crit h3{color:var(--crit)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}
.card{background:var(--panel2);border:1px solid var(--border);border-radius:4px;padding:16px 18px;text-align:center}
.card .n{font-size:30px;font-weight:700;line-height:1}
.card .l{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin-top:6px}
.card.crit .n{color:var(--crit)} .card.high .n{color:var(--high)} .card.med .n{color:var(--med)} .card.low .n{color:var(--low)}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-bottom:8px}
table.grid th{text-align:left;color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.08em;padding:8px 10px;border-bottom:1px solid var(--border)}
table.grid td{padding:10px;border-bottom:1px solid var(--border);vertical-align:top}
table.grid tr:hover td{background:rgba(255,255,255,.015)}
.mono{font-family:"SF Mono",ui-monospace,Menlo,monospace;font-size:12.5px}
.mono.beta{color:var(--beta)} .dim{color:var(--faint)} .beta{color:var(--beta)}
code{font-family:"SF Mono",ui-monospace,Menlo,monospace;font-size:.88em;background:rgba(77,163,255,.10);color:#9ec9ff;padding:1px 5px;border-radius:3px;border:1px solid rgba(77,163,255,.15)}
.badge{display:inline-block;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:2px 8px;border-radius:3px}
.b-reuse{background:rgba(63,185,80,.15);color:#56d364;border:1px solid rgba(63,185,80,.3)}
.b-isolate{background:rgba(255,92,92,.13);color:#ff7b7b;border:1px solid rgba(255,92,92,.3)}
.b-clone{background:rgba(199,146,234,.14);color:var(--beta);border:1px solid rgba(199,146,234,.3)}
.b-depends{background:rgba(245,215,110,.12);color:var(--med);border:1px solid rgba(245,215,110,.3)}
details.phase{background:var(--panel);border:1px solid var(--border);border-radius:4px;margin-bottom:10px;overflow:hidden}
details.phase summary{cursor:pointer;padding:16px 20px;font-weight:600;list-style:none;display:flex;align-items:center;gap:14px;user-select:none}
details.phase summary::-webkit-details-marker{display:none}
details.phase summary:hover{background:rgba(255,255,255,.02)}
details.phase[open] summary{border-bottom:1px solid var(--border)}
.pnum{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:4px;background:var(--accent);color:#06121f;font-size:13px;font-weight:800;flex:0 0 auto}
.ptitle{font-size:14.5px}
.phase-body{padding:18px 22px 22px}
.goal{color:var(--dim)} .verify{color:var(--ok);background:rgba(63,185,80,.06);padding:10px 14px;border-radius:4px;border-left:2px solid var(--ok);margin-top:6px}
ol.steps{margin:6px 0;padding-left:22px} ol.steps li{margin-bottom:9px}
ul.tight{margin:4px 0;padding-left:20px} ul.tight li{margin-bottom:7px}
ul.risk li{color:#ffb3b3}
.where{font-size:11.5px;color:var(--faint);margin-top:4px;font-style:italic}
.sev{display:inline-block;font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;padding:2px 7px;border-radius:3px}
.sev.sev-critical{background:rgba(255,92,92,.16);color:#ff7b7b;border:1px solid rgba(255,92,92,.35)}
.sev.sev-high{background:rgba(255,159,67,.14);color:var(--high);border:1px solid rgba(255,159,67,.3)}
.sev.sev-medium{background:rgba(245,215,110,.12);color:var(--med);border:1px solid rgba(245,215,110,.3)}
.sev.sev-low{background:rgba(106,169,255,.12);color:var(--low);border:1px solid rgba(106,169,255,.3)}
tr.sev-critical td{background:rgba(255,92,92,.035)}
.cat{color:var(--faint);font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
.fix{color:var(--dim)}
.toc{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.toc a{font-size:12.5px;color:var(--dim);text-decoration:none;padding:5px 12px;border:1px solid var(--border);border-radius:20px;background:var(--panel2)}
.toc a:hover{color:var(--accent);border-color:var(--accent)}
footer{margin-top:60px;padding-top:20px;border-top:1px solid var(--border);color:var(--faint);font-size:12px}
.locked{margin:0 0 8px}
.locked .lhead{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--beta);font-weight:700;margin-bottom:12px}
.lgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.lcard{background:linear-gradient(160deg,rgba(199,146,234,.07),var(--panel));border:1px solid rgba(199,146,234,.25);border-radius:4px;padding:16px 18px}
.lhdr{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);font-weight:600}
.lchoice{font-size:15px;font-weight:700;color:var(--beta);margin:5px 0 8px}
.lnote{font-size:12.5px;color:var(--dim);line-height:1.5}
.p0grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-bottom:8px}
.p0card{border:1px solid var(--border);border-radius:4px;padding:14px 16px;background:var(--panel)}
.p0card.fix{border-left:3px solid var(--accent)}
.p0card.ok{border-left:3px solid var(--ok)}
.p0card.risk{border-left:3px solid var(--crit)}
.p0t{font-weight:700;font-size:13.5px;margin-bottom:6px}
.p0card.fix .p0t{color:var(--accent)} .p0card.ok .p0t{color:var(--ok)} .p0card.risk .p0t{color:#ff7b7b}
.p0d{font-size:12.5px;color:var(--dim);line-height:1.55}
.p0lab{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);font-weight:700;margin:18px 0 10px}
</style></head>
<body><div class="wrap">

<header class="top">
  <div class="eyebrow">Radon &middot; Staging Environment Plan</div>
  <h1><span class="b">beta</span>.radon.run &mdash; carbon copy of app.radon.run</h1>
  <div class="sub">Reuse-maximizing parallel staging stack, co-located on the production Hetzner VPS. Plan synthesized + adversarially scrutinized twice.</div>
  <div class="meta">
    <span><b>Generated</b> ${esc(new Date().toISOString().slice(0,16).replace('T',' '))} UTC</span>
    <span><b>Method</b> 7 subsystem analysts &rarr; architect &rarr; 2 adversarial passes &rarr; synthesis (11 agents)</span>
    <span><b>Phases</b> ${F.orderedSteps.length}</span>
    <span><b>Known issues</b> ${issues.length}</span>
  </div>
</header>

<section class="locked">
  <div class="lhead">&#9679; Locked decisions (operator)</div>
  <div class="lgrid">${lockedHtml}</div>
</section>

<nav class="toc">
  <a href="#phase0">Phase 0 ground truth</a>
  <a href="#summary">Summary</a><a href="#arch">Architecture</a><a href="#ports">Port map</a>
  <a href="#reuse">Reuse matrix</a><a href="#env">Env delta</a><a href="#steps">Steps</a>
  <a href="#issues">Known issues</a><a href="#decisions">Open decisions</a>
  <a href="#cost">Cost &amp; teardown</a><a href="#appendix">Appendix</a>
</nav>

<section id="phase0">
<h2>Phase 0 &mdash; VPS ground truth (captured 2026-06-01 04:18Z, read-only)</h2>
<div class="p0lab">&#9650; Corrections to the plan (live box differs from assumptions)</div>
<div class="p0grid">${p0group(PHASE0.corrections, 'fix')}</div>
<div class="p0lab">&#10003; Confirmed</div>
<div class="p0grid">${p0group(PHASE0.confirmed, 'ok')}</div>
<div class="p0lab">&#9888; Hazards &amp; unresolved (operator action)</div>
<div class="p0grid">${p0group(PHASE0.risks, 'risk')}</div>
</section>

<nav class="toc" style="display:none">
  <a href="#summary">Summary</a><a href="#arch">Architecture</a><a href="#ports">Port map</a>
  <a href="#reuse">Reuse matrix</a><a href="#env">Env delta</a><a href="#steps">Steps</a>
  <a href="#issues">Known issues</a><a href="#decisions">Open decisions</a>
  <a href="#cost">Cost &amp; teardown</a><a href="#appendix">Appendix</a>
</nav>

<section id="summary">
<h2>Executive summary</h2>
<div class="panel">${rich(F.executiveSummary)}</div>
</section>

<section id="arch">
<h2>Recommended architecture</h2>
<div class="panel">${rich(F.recommendedArchitecture)}</div>
</section>

<section id="issues-overview">
<div class="cards">
  <div class="card crit"><div class="n">${sevCounts.critical||0}</div><div class="l">Critical</div></div>
  <div class="card high"><div class="n">${sevCounts.high||0}</div><div class="l">High</div></div>
  <div class="card med"><div class="n">${sevCounts.medium||0}</div><div class="l">Medium</div></div>
  <div class="card low"><div class="n">${sevCounts.low||0}</div><div class="l">Low</div></div>
  <div class="card"><div class="n">${F.orderedSteps.length}</div><div class="l">Phases</div></div>
  <div class="card"><div class="n">${F.openDecisions.length}</div><div class="l">Decisions</div></div>
</div>
</section>

<section id="ports">
<h2>Port map</h2>
<table class="grid"><thead><tr><th>Service</th><th>Prod port</th><th>Beta port</th></tr></thead><tbody>${portRows}</tbody></table>
</section>

<section id="reuse">
<h2>Reuse matrix</h2>
<table class="grid"><thead><tr><th>Component</th><th>Decision</th><th>Detail</th></tr></thead><tbody>${reuseRows}</tbody></table>
</section>

<section id="env">
<h2>Environment delta</h2>
<table class="grid"><thead><tr><th>Key</th><th>File</th><th>Prod</th><th>Beta</th><th>Note</th></tr></thead><tbody>${envRows}</tbody></table>
</section>

<section id="steps">
<h2>Ordered implementation steps</h2>
${stepsHtml}
</section>

<section id="issues">
<h2>Known issues &mdash; edge cases &amp; footguns (severity-sorted)</h2>
<table class="grid"><thead><tr><th>Sev</th><th>Category</th><th>Issue</th><th>Fix</th></tr></thead><tbody>${issueRows}</tbody></table>
</section>

<section id="decisions">
<h2>Open decisions (you must make these)</h2>
<div class="panel"><ol class="steps">${decisionItems}</ol></div>
</section>

<section id="cost">
<h2>Cost notes &amp; teardown</h2>
<div class="callout"><h3>Cost</h3><div>${rich(F.costNotes)}</div></div>
<div class="callout"><h3>Teardown plan</h3><div>${rich(F.teardownPlan)}</div></div>
</section>

<section id="appendix">
<h2>Appendix &mdash; adversarial passes &amp; subsystem analyses</h2>
${scrutinyBlock(data.scrutiny1, 'Scrutiny Pass 1')}
${scrutinyBlock(data.scrutiny2, 'Scrutiny Pass 2')}
${subsysBlock}
</section>

<footer>
Generated by multi-agent workflow <code>beta-radon-staging-plan</code> &middot; ${data.subsystemMaps.length} subsystem maps, scrutiny passes ${data.scrutiny1.findings.length}+${data.scrutiny2.findings.length} raw findings &rarr; ${issues.length} consolidated.
Plan-asserted file paths require Phase-0 on-VPS verification before execution.
</footer>

</div></body></html>`;

fs.writeFileSync(OUT, html);
console.log('WROTE ' + OUT + ' (' + html.length + ' bytes)');
