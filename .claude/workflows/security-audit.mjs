export const meta = {
  name: 'security-audit',
  description: 'Reusable Radon security audit: fan out N dimensions, adversarially verify each finding, run a completeness + regression critic. Returns structured JSON.',
  whenToUse: 'Run a comprehensive security audit of the Radon app, or re-verify that past fixes have not regressed. Extend by editing the DIMENSIONS catalog below.',
  phases: [
    { title: 'Audit', detail: 'one finder per security dimension' },
    { title: 'Verify', detail: 'adversarially verify each finding for real exploitability' },
    { title: 'Critic', detail: 'completeness + regression critic' },
  ],
}

// ===========================================================================
// HOW TO RUN
//   Workflow({ name: 'security-audit' })                      // full audit
//   Workflow({ name: 'security-audit', args: { focus: ['authn-authz','sqli'] } })  // subset
//   Workflow({ name: 'security-audit', args: { extraDimensions: [{ key, label, scope }] } })  // extend
// Then turn the returned JSON into HTML:
//   python3 scripts/security/gen_security_report.py <task-output.json> docs/security-audit-<date>.html
//
// HOW TO EXTEND (this is the point — add to it over time)
//   1. Add an entry to DIMENSIONS (key/label/scope). Keep scope concrete: name
//      the files to read and the exact weaknesses to hunt.
//   2. Add any new durable invariant to the REGRESSION_INVARIANTS list so every
//      future run re-checks that a past fix has not been undone.
//   3. Mirror the change in docs/security-audit-playbook.md.
// ===========================================================================

const REPO = '/Users/joemccann/dev/apps/finance/radon'

const PREAMBLE = `You are a senior application-security engineer auditing RADON, a production options/equities trading app for a single operator. Repo root: ${REPO}.

ARCHITECTURE (ground truth — build on it, don't rediscover):
- Next.js 16 frontend in web/ (~86 API route.ts handlers under web/app/api). Auth via Clerk. Edge middleware web/middleware.ts is DEFAULT-DENY: every page + /api/* needs a Clerk session EXCEPT an explicit allowlist (sign-in/up, a fixed PUBLIC_SHARE_API_ROUTES list, /api/webhooks/clerk (svix-verified), /api/service-health, /api/health, bearer-gated /api/probe/freshness). On top of authentication is an AUTHORIZATION layer: ALLOWED_USER_IDS operator allowlist -> non-operators get 403. The /api matcher ALWAYS runs middleware (separate matcher entry). Local-dev bypass only when NODE_ENV!=='production' on localhost; RADON_AUTHLESS_TEST=1 for Playwright.
- FastAPI on localhost:8321 (scripts/api/). auth.py: Clerk JWT (RS256/JWKS) OR trusted-local bypass (is_trusted_local_request = loopback/tailnet AND NOT arrived via reverse-proxy forwarding headers) OR scoped X-API-Key (MDW_API_KEY, hmac.compare_digest, 3 read paths). AUTH_EXEMPT_PATHS is pinned by test_health_payload.py + test_route_authz_matrix.py. DB access from FastAPI is HTTP-only via db_http.py (libsql import banned by AST lint).
- Node WS relay (scripts/ib_realtime_server.js, :8765) authed by short-TTL tickets (scripts/api/ws_ticket.py, 30s). Trust decision in scripts/lib/wsTrust.js: trusted iff loopback AND no forwarding header (Caddy proxies /ws* to loopback, so peer address alone is NOT trustworthy).
- DB: Turso (libsql). Next.js reads via web/lib/db.ts getDb() (HTTPS transport). Migrations scripts/db/migrations/.
- Deploy: push to main -> .github/workflows/ci.yml runs tests then SSHes to Hetzner and runs scripts/deploy.sh from the separate radon-cloud repo. Reverse proxy is Caddy (docker/caddy/Caddyfile is the in-repo reference; the LIVE Caddyfile is in radon-cloud on the VPS and may differ — flag what can't be verified here). IB Gateway in Docker.

RUN THESE FRESH (don't trust cached counts): cd ${REPO}/web && npm audit --omit=dev --json ; git -C ${REPO} log --all -p | grep for leaked secrets ; rg for the sinks named in your dimension.

YOUR JOB: find REAL security weaknesses in YOUR assigned dimension. For each, establish concrete exploitability (who is the attacker, what they send, what they get) for a single-operator app exposed at app.radon.run. Cite exact file:line. Read the actual code. Distinguish a true vulnerability from a defense-in-depth nit (mark the latter info/low). It is FINE to report an area is clean. Do NOT modify any files — analysis only. Prefer rg + targeted reads.`

const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['findings', 'areas_checked_clean'],
  properties: {
    findings: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      required: ['title','severity','category','file','line','description','exploitability','evidence','recommendation','patch_complexity','confidence'],
      properties: {
        title: { type: 'string' },
        severity: { type: 'string', enum: ['critical','high','medium','low','info'] },
        category: { type: 'string', description: 'CWE name or OWASP category' },
        file: { type: 'string' }, line: { type: 'string' },
        description: { type: 'string' },
        exploitability: { type: 'string', description: 'concrete attack path, or why it is only defense-in-depth' },
        evidence: { type: 'string' }, recommendation: { type: 'string' },
        patch_complexity: { type: 'string', enum: ['trivial','moderate','risky'] },
        confidence: { type: 'string', enum: ['high','moderate','low'] },
      },
    } },
    areas_checked_clean: { type: 'string' },
  },
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['is_real','exploitable','revised_severity','justification'],
  properties: {
    is_real: { type: 'boolean' },
    exploitable: { type: 'boolean' },
    revised_severity: { type: 'string', enum: ['critical','high','medium','low','info','false-positive'] },
    justification: { type: 'string' },
  },
}

// --- The dimension catalog. EDIT THIS to extend the audit. ---
const DIMENSIONS = [
  { key: 'authn-authz', label: 'auth/authz', scope: `Authentication & authorization. Read web/middleware.ts, scripts/api/auth.py, scripts/api/ws_ticket.py, web/lib/probeAuth.ts, web/lib/demo/*, every scripts/api/routes/*, and a sample of web/app/api/**/route.ts. Hunt: middleware matcher gaps, JWT validation flaws (alg confusion, missing aud/issuer/exp, verify_aud:false), trusted-local bypass spoofable via forwarding headers, ALLOWED_USER_IDS gaps, routes not enforcing authz, IDOR on per-user resources (bookmarks/profile/watchlist — scoped to user_id or global?), admin routes (web/app/api/admin/*, stack/restart, ib/restart, services/[unit]/[action]), demo gate bypass, share routes leaking operator data.` },
  { key: 'sqli', label: 'sql-injection', scope: `SQL injection & query safety. Read web/lib/{db,dbExecute,dbFirstRead,journalDb,dbCache}.ts, scripts/api/db_http.py, scripts/db/{writer.js,writer.py,client.py}, and EVERY db.execute / hrana_execute / cursor.execute call site (rg). Hunt: string/f-string/template-literal SQL with user-derived values, dynamic table/column names from input, missing parameterization, ORDER BY/LIMIT injection. Trace user input from params/query/body into queries.` },
  { key: 'cmd-path-ssrf', label: 'cmd/path/ssrf', scope: `Command injection, path traversal, SSRF. Read scripts/api/subprocess.py (run_script allowlist), the PI exec allowlist in scripts/api/server.py (_PI_READ_SCRIPTS/_PI_MUTATE_SCRIPTS), web/app/api/menthorq/cta/route.ts (spawn), web/app/api/pi/route.ts. Hunt: shell=True / spawn-with-shell / string-built commands with user input, allowlist bypass, path traversal in routes reading/writing files from user input (watchlist/[symbol], bookmarks/[post_id], backtest/[strategy], flow-analysis/[ticker], ticker/*, futures/chain, any join(dir,userInput)/readFile of user path), SSRF in server-side fetch/radonFetch/scripts/clients/* where a URL/host is influenced by input.` },
  { key: 'secrets', label: 'secrets', scope: `Secrets & credential exposure. Read .env.example, web/.env.example, scripts/api/tests/test_no_secret_leakage.py. rg for: console.log/logger of tokens/passwords/auth headers, non-NEXT_PUBLIC secrets referenced in 'use client' code or returned in API JSON, error responses echoing env/stack/paths, secrets in URLs, /api/flex-token exposure, Clerk secret vs publishable. Re-verify no secrets are committed (git ls-files) and none in git history.` },
  { key: 'client-xss', label: 'client/xss/headers', scope: `Client XSS, CSP, security headers, CORS, open redirect, clickjacking. Read web/next.config.* (CSP/headers), docker/caddy/Caddyfile, web/components/ThemeBootstrap.tsx. rg for: dangerouslySetInnerHTML, innerHTML, document.write, href/src from data, window.location/redirect from user params, target=_blank w/o noopener, postMessage w/o origin check, localStorage/sessionStorage of tokens, missing CSP/X-Frame-Options/HSTS/X-Content-Type-Options, permissive CORS. Do share/content routes render untrusted data into HTML/SVG?` },
  { key: 'api-idor-validation', label: 'api-validation/idor', scope: `API input validation, IDOR, mass assignment, rate limiting on MUTATING routes: orders/{place,cancel,modify}, paper/place, journal/sync, watchlist, bookmarks, profile, alerts, admin/*, workflow/run + FastAPI counterparts. Hunt: missing/loose validation, mass assignment (spreading req body into DB/IB writes), IDOR (acting on resources by id w/o ownership check), no rate limiting on expensive/abusable endpoints (/api/assistant, /api/pi, scan triggers, order placement), CSRF posture for cookie-authed POSTs, server-side bypass of risk gates via param tampering on qty/price.` },
  { key: 'vps-infra-deploy', label: 'vps/infra/deploy', scope: `VPS/infra/deploy/container security. Read .github/workflows/*.yml, docker/**/docker-compose.yml + Dockerfiles, docker/caddy/Caddyfile, scripts/deploy.sh + setup-vps.sh if present. Hunt: GitHub Actions risks (untrusted input in run:, pull_request_target, secrets to forks, third-party actions not SHA-pinned, broad permissions), SSH deploy key handling, Docker risks (privileged, host network, exposed ports to 0.0.0.0, baked secrets, :latest tags), Caddy misconfig (handle_path /api/ib/* exposure), world-readable secret files. Live Caddy + radon-cloud may not be in this tree — flag unverifiable items.` },
  { key: 'dependencies', label: 'dependencies', scope: `Dependency & supply-chain. Run (cd ${REPO}/web && npm audit --omit=dev --json) and parse; check requirements.txt/pyproject.toml for unpinned/known-bad versions. For each critical/high npm vuln: name the package, decide if it is runtime-reachable/exploitable in THIS app's usage (transitive build-time dep vs network-facing parser), and the fix (version bump/override). Verify GitHub Actions third-party actions are SHA-pinned. Note bun.lock vs package-lock.json (project mandates bun). Prioritize runtime-reachable, network-facing vulns.` },
  { key: 'websocket-relay', label: 'websocket/relay', scope: `WebSocket relay & realtime auth. Read scripts/ib_realtime_server.js, scripts/lib/wsTrust.js, scripts/api/ws_ticket.py, web/app/api/ib/ws-ticket/route.ts, web/lib/usePrices.ts. Hunt: ticket forgeable/replayable (TTL, single-use, signed?), trusted-local check bypassable (forwarding headers), missing origin/host check on upgrade (cross-site WS hijack), unauthenticated subscribe to arbitrary symbols/depth, message parsing without validation (DoS via malformed frames / no maxPayload), 0.0.0.0 bind, account/order data over an unauthenticated channel, no subscription rate limit.` },
  { key: 'data-pii', label: 'data/pii', scope: `Data exposure & PII. Read share content routes (web/app/api/*/share/content/route.ts + web/lib/shareReportPath.ts) — do they publish operator account figures/positions/PII to unauthenticated callers? Read web/lib/demo/* for demo-vs-operator isolation, routes returning account_summary/account IDs, /health trust-scoping. Hunt: account numbers/balances/positions reachable without operator authz, demo users seeing operator data, share routes with IDOR (guessable ids returning private snapshots), verbose errors leaking holdings, PII in logs.` },
  // --- Regression dimension: re-checks that past fixes have NOT been undone. ---
  { key: 'regression', label: 'regression', scope: `REGRESSION CHECK — verify each durable invariant from prior audits still holds. Report a finding ONLY if an invariant has REGRESSED.
1. scripts/lib/wsTrust.js: shouldSkipTicketValidation must return false when a forwarding header (x-forwarded-for/x-real-ip/forwarded/x-forwarded-host) is present, even for loopback; ib_realtime_server.js must call it (not trust remoteAddress alone).
2. web/lib/shareReportPath.ts: the 5 */share/content routes must serve ONLY tweet-<type>-<date>[-card-N].html from directly inside reports/ (no arbitrary reports/ paths, no traversal). Each route scoped to its own type.
3. scripts/api/server.py AUTH_EXEMPT_PATHS must NOT contain /docs or /openapi.json (trusted-local only); the two pin tests must still match.
4. web/middleware.ts: default-deny (if(!isPublicRoute)), ALLOWED_USER_IDS operator gate, the /api matcher always running, PUBLIC_SHARE_API_ROUTES as a FIXED explicit list (no /api/**/share regex).
5. /health + /api/service-health must be trust-scoped / scrubbed (no account IDs, IB topology, or secret-shaped error strings to anonymous callers).
6. No spawn() from Next.js with user-controlled args; subprocess + PI exec allowlists intact.
Read the cited files and confirm. A clean run reports zero findings here.` },
]

const baseDims = (args && Array.isArray(args.focus) && args.focus.length)
  ? DIMENSIONS.filter((d) => args.focus.includes(d.key))
  : DIMENSIONS
const extra = (args && Array.isArray(args.extraDimensions)) ? args.extraDimensions : []
const ACTIVE = baseDims.concat(extra)

function finderPrompt(d) {
  return `${PREAMBLE}\n\n=== YOUR DIMENSION: ${d.key} ===\n${d.scope}\n\nReport every genuine finding with exact file:line and a concrete exploitability assessment. Also report defense-in-depth items (info/low), marked as such. This is the only pass for this dimension; be exhaustive.`
}
function verifyPrompt(f, d) {
  return `${PREAMBLE}\n\nYou are an ADVERSARIAL VERIFIER. Another auditor reported the finding below in dimension "${d.key}". REFUTE it. Read the cited code yourself (${f.file}:${f.line}) and surrounding context — do not trust the report. Is it real or a false positive (server-controlled input, upstream gate, unreachable path, middleware already blocks it)? Is it ACTUALLY exploitable against app.radon.run, or only theoretical/defense-in-depth? Default to skepticism: if you cannot construct a concrete exploit, mark exploitable=false and lower severity. Be honest if it holds.\n\nFINDING\nTitle: ${f.title}\nSeverity: ${f.severity} (confidence ${f.confidence})\nCategory: ${f.category}\nFile: ${f.file}:${f.line}\nDescription: ${f.description}\nClaimed exploitability: ${f.exploitability}\nEvidence: ${f.evidence}\n\nReturn your verdict via the schema.`
}

const audited = await pipeline(
  ACTIVE,
  (d) => agent(finderPrompt(d), { label: `find:${d.label}`, phase: 'Audit', schema: FINDINGS_SCHEMA, model: 'sonnet' }),
  (review, d) => {
    const findings = (review && review.findings) || []
    if (findings.length === 0) return { dimension: d.key, areas_clean: review ? review.areas_checked_clean : '(finder returned null)', verified: [] }
    return parallel(findings.map((f) => () =>
      agent(verifyPrompt(f, d), { label: `verify:${d.label}`, phase: 'Verify', schema: VERDICT_SCHEMA })
        .then((v) => ({ ...f, dimension: d.key, verdict: v }))
        .catch(() => ({ ...f, dimension: d.key, verdict: null }))
    )).then((verified) => ({ dimension: d.key, areas_clean: review.areas_checked_clean, verified: verified.filter(Boolean) }))
  }
)

const byDim = audited.filter(Boolean)
const allVerified = byDim.flatMap((b) => b.verified)
const isConfirmed = (f) => f.verdict && f.verdict.is_real && f.verdict.revised_severity !== 'false-positive'
const confirmed = allVerified.filter(isConfirmed)
const falsePositives = allVerified.filter((f) => !isConfirmed(f))
log(`Audit: ${allVerified.length} raw, ${confirmed.length} confirmed, ${falsePositives.length} false positives across ${byDim.length} dimensions`)

phase('Critic')
const confirmedDigest = confirmed.map((f) => `[${f.verdict.revised_severity}] ${f.dimension}: ${f.title} (${f.file}:${f.line})`).join('\n')
const critic = await agent(
  `${PREAMBLE}\n\nYou are the COMPLETENESS CRITIC. Confirmed findings so far:\n${confirmedDigest || '(none)'}\n\nFind what was MISSED across the whole attack surface of a single-operator trading app that places real money orders: a route/attack-class no dimension owned; auth seams between Next.js middleware and FastAPI; the order-placement path bypassing server-side risk gates; business-logic abuse (replay, races on order/cancel); rate-limiting/DoS on expensive endpoints; webhook signature verification. Report NEW findings only (structured schema); do not repeat confirmed ones. If coverage is complete, say so in areas_checked_clean and return empty findings.`,
  { label: 'completeness-critic', phase: 'Critic', schema: FINDINGS_SCHEMA }
)
const criticFindings = (critic && critic.findings) || []
const criticVerified = await parallel(criticFindings.map((f) => () =>
  agent(verifyPrompt(f, { key: 'completeness-critic' }), { label: 'verify:critic', phase: 'Critic', schema: VERDICT_SCHEMA })
    .then((v) => ({ ...f, dimension: 'completeness-critic', verdict: v }))
    .catch(() => ({ ...f, dimension: 'completeness-critic', verdict: null }))
))
const criticConfirmed = criticVerified.filter(isConfirmed)
log(`Critic added ${criticConfirmed.length} confirmed findings`)

return {
  generated_for: 'radon-security-audit',
  dimensions_run: ACTIVE.map((d) => d.key),
  confirmed: confirmed.concat(criticConfirmed),
  false_positives: falsePositives.map((f) => ({ title: f.title, file: f.file, dimension: f.dimension, reason: f.verdict ? f.verdict.justification : 'verifier error' })),
  clean_areas: byDim.map((b) => ({ dimension: b.dimension, summary: b.areas_clean })),
  critic_clean: critic ? critic.areas_checked_clean : null,
  counts: { raw: allVerified.length, confirmed: confirmed.length + criticConfirmed.length, false_positives: falsePositives.length, critic_new: criticConfirmed.length },
}
