export const meta = {
  name: 'security-audit-knowledge',
  description: 'Focused security audit of the Radon knowledge-base subsystem (Phases 0-3): SQLi, prompt injection, MCP trust boundary, data isolation, ingest, secrets. Finder -> adversarial verifier -> critic.',
  whenToUse: 'Audit the new knowledge base (scripts/knowledge/*, KB routes, MCP server, assistant tools).',
  phases: [
    { title: 'Audit', detail: 'one finder per KB security dimension' },
    { title: 'Verify', detail: 'adversarially verify each finding' },
    { title: 'Critic', detail: 'completeness critic over the KB attack surface' },
  ],
}

const REPO = '/Users/joemccann/dev/apps/finance/radon'

const PREAMBLE = `You are a senior application-security engineer auditing the NEWLY ADDED knowledge-base (KB) subsystem of RADON, a production single-operator options/equities trading app. Repo root: ${REPO}.

WHAT THE KB IS (ground truth — verified by the lead auditor; build on it, don't rediscover):
- A RAG/retrieval system. Python pipeline in scripts/knowledge/: schema.py, store.py (writers), retrieve.py (hybrid FTS5 BM25 + vector + recency fusion), embed.py (LOCAL fastembed ONNX model, ~67MB, journal text never leaves the box), distill.py (LLM distillation via Cerebras), ingest.py (connector fetch -> pre-filter -> distill -> embed -> upsert), sources/* connectors (journal, newsfeed, evals, incidents, docs), mcp_server.py (read-only stdio MCP for Claude Code), eval_golden.py. Migration scripts/db/migrations/0028_knowledge.sql (knowledge table + knowledge_fts FTS5 + vector ANN index idx_knowledge_embedding).
- Surfaces: FastAPI POST /knowledge/search + GET /knowledge/prior-evals (scripts/api/server.py ~4041-4240); Next.js proxies web/app/api/knowledge/{search,prior-evals}/route.ts; assistant tools web/lib/assistant/tools.ts (search_knowledge, find_prior_evals) invoked by /api/assistant; the stdio MCP server for Claude Code.

ALREADY-ESTABLISHED FACTS (verify they hold; do NOT re-report as novel unless you find they are WRONG):
- AUTH: KB Next.js routes are NOT in web/middleware.ts public allowlist -> protected by default-deny + ALLOWED_USER_IDS operator gate. FastAPI /knowledge/* are NOT in AUTH_EXEMPT_PATHS (server.py ~559) -> require Clerk JWT or trusted-local.
- DEMO ISOLATION: FastAPI knowledge_search + knowledge_prior_evals short-circuit to empty results when test_mode is true (the demo instance runs RADON_API_TEST_MODE=1). The corpus carries real journal/eval figures, so the demo tier must get nothing.
- SQLi: retrieve.py and store.py use ? placeholders throughout; fts_match_expression() strips FTS5 syntax to quoted [A-Za-z0-9_]+ tokens OR'd; filter column names are hardcoded ("scope"/"source"), IN(...) uses ?-marks. distill.py POSTs to a HARDCODED Cerebras URL (no SSRF); the API key is never logged.

YOUR JOB: find REAL security weaknesses in YOUR assigned dimension in the KB code. For each, establish concrete exploitability: WHO is the attacker (remember: the search surface is operator-only; the only attacker-influenceable INGEST source is NEWSFEED — scraped external articles — plus anything an external party can get written into journal/incidents/evals), WHAT they inject/send, and WHAT they get or cause. Cite exact file:line, read the ACTUAL code (rg + targeted reads). Distinguish a true vulnerability from a defense-in-depth nit (mark the latter info/low). It is FINE to report an area is clean. Do NOT modify files — analysis only.`

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
        category: { type: 'string' },
        file: { type: 'string' }, line: { type: 'string' },
        description: { type: 'string' },
        exploitability: { type: 'string' },
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

const DIMENSIONS = [
  { key: 'sqli-fts-vector', label: 'sqli/fts/vector', scope: `SQL / FTS5 / vector injection across the ENTIRE KB SQL surface. Read retrieve.py (every db.execute, fts_match_expression, _knowledge_filters, _FTS_SQL_TEMPLATE, _VECTOR_TOP_K_*), store.py (upsert/delete/prune, the ?-mark builders), ingest.py (_EXISTING_SQL, pre-filter reads), sources/*.py (_ROWS_SQL/_BATCH_SQL), 0028_knowledge.sql, mcp_server.py (_latest_rows, its SQL). Hunt: any SQL string built with a value NOT bound via ?; dynamic column/table/ORDER BY from input; FTS5 MATCH injection surviving fts_match_expression (can a query break out of the quoted-token OR list? unicode/quote/backslash tricks?); vector32()/vector_top_k() arg injection; the ?-mark join count mismatching args (arg/placeholder desync). Confirm the lead's "parameterized" claim adversarially — find the ONE place it isn't.` },
  { key: 'prompt-injection', label: 'prompt-injection', scope: `Indirect prompt injection through ingested content. The KB ingests NEWSFEED (scraped external articles — attacker-influenceable), journal, incidents, evals, docs. Content flows: (a) distill.py sends raw content to an LLM (Cerebras) that returns a summary stored in the KB; (b) retrieve.py returns title/summary/content/neighbors to consumers; (c) consumers are the operator's ASSISTANT (web/lib/assistant/tools.ts -> /api/assistant, Claude) and CLAUDE CODE (mcp_server.py). Read distill.py (_SYSTEM_PROMPT, _request_payload, _parse_distillation — can crafted content coerce the model into emitting attacker-chosen summary/tickers, or break the JSON contract?), web/lib/assistant/tools.ts + web/app/api/assistant/route.ts (is retrieved KB content labeled/delimited as UNTRUSTED DATA, or spliced into the prompt as if trusted? can a poisoned newsfeed 'summary' instruct the assistant to exfiltrate/act?), mcp_server.py tool descriptions/outputs. Hunt: no data/instruction separation on tool results; distilled summary trusted downstream; tickers[] used unsanitized in later queries/UI; injection that survives distillation into search output. Rate realistically for a single-operator app (attacker must poison a scraped source; consumer is Claude), but this is the KB's marquee risk — be concrete about the end-to-end path.` },
  { key: 'mcp-trust', label: 'mcp-trust', scope: `MCP server trust boundary. Read scripts/knowledge/mcp_server.py IN FULL. It is described as READ-ONLY stdio over the PRODUCTION DB. Verify: (1) is the read-only guarantee ENFORCED (a SQL write-keyword check? does it actually block writes, and can the check be bypassed — comments, CTEs, PRAGMA, ATTACH, multi-statement)? or is 'read-only' just achieved by only issuing SELECTs (fine) vs an ineffective blocklist (bad)? (2) stdio transport = local Claude Code only, no network bind — confirm no port/host/0.0.0.0. (3) _connect_production_db: does it read/write real prod Turso? any replica/credential handling issue? (4) tool inputs (ticker/query/limit/source/scope) validated + clamped (_clamped_limit, _unknown_values_error)? (5) do tool OUTPUTS leak secrets/PII beyond what the operator should see via Claude Code? (6) can a poisoned KB doc returned by a tool drive Claude Code to take destructive actions (tie to prompt-injection)?` },
  { key: 'data-isolation-pii', label: 'isolation/pii', scope: `Data isolation & PII in the KB. The corpus carries REAL journal trades, eval figures, incident writeups, account-adjacent data. Confirm the test_mode empty-result gate in server.py knowledge_search/knowledge_prior_evals is UNCONDITIONAL and cannot be bypassed (query params, scopes/sources forcing a non-empty path, the prior-evals GET vs the search POST, the assistant tool path, a direct FastAPI hit from the demo box). Read sources/journal.py + sources/evals.py + sources/incidents.py: what fields land in the KB (raw account ids? P&L? secrets in incident last_error strings?)? Does distill.py or the stored summary/metadata carry secret-shaped strings (Turso URL, tokens, account U-ids) that then surface in search results / MCP / assistant with no scrub? Is there any scope that a non-operator-but-authenticated demo user could reach? Cross-check against the app's scrubSecrets chokepoints — KB output does NOT flow through them.` },
  { key: 'ingest-dos-supply', label: 'ingest/dos/supply-chain', scope: `Ingest pipeline robustness, DoS, resource, supply chain. Read ingest.py (fetch -> pre-filter -> distill -> embed -> upsert; pagination, batch, prune authority, the vanished-doc prune guard — can a connector returning [] wrongly prune the whole corpus? is host-local prune authority correctly withheld?), embed.py (fastembed ONNX model download on first use — from where? pinned/verified? trust_remote_code? memory/CPU blowup on huge content? thread-safety of the singleton), distill.py (MAX_CONTENT_CHARS truncation, timeout, retry — unbounded content? cost/DoS via a giant scraped doc?), the radon-knowledge.timer/service unit (cadence, isolation, RADON_DB_NO_REPLICA, resource limits). Hunt: prune-the-world bug, model supply-chain (unpinned/unverified download, remote-code), unbounded content/embedding cost, connector error handling that corrupts the corpus, a poisoned oversized newsfeed doc wedging the ingest timer.` },
  { key: 'validation-authz-routes', label: 'route-validation/authz', scope: `KB route input validation & authorization at the web edge. Read web/app/api/knowledge/{search,prior-evals}/route.ts, the FastAPI _validated_knowledge_query/_validated_knowledge_filter/_clamped_knowledge_limit + the route handlers, web/lib/assistant/tools.ts (search_knowledge/find_prior_evals arg handling). Hunt: missing/loose validation letting a huge/negative limit, huge scopes/sources arrays (amplification into the vector overfetch pool*4), unvalidated compact/scope/source reaching the DB legs, the prior-evals ticker regex bounds, mass-assignment of the raw body into the FastAPI search (body spread?), any auth assumption that breaks if middleware is bypassed, CSRF posture on the POST, error responses leaking internals/stack. Confirm the operator-only + test_mode gates actually cover the assistant-tool path too.` },
]

function finderPrompt(d) {
  return `${PREAMBLE}\n\n=== YOUR DIMENSION: ${d.key} ===\n${d.scope}\n\nReport every genuine finding with exact file:line and a concrete exploitability assessment. Also report defense-in-depth items (info/low), marked as such. Be exhaustive; this is the only finder pass for this dimension.`
}
function verifyPrompt(f, d) {
  return `${PREAMBLE}\n\nYou are an ADVERSARIAL VERIFIER. Another auditor reported the finding below in KB dimension "${d.key}". REFUTE it. Read the cited code yourself (${f.file}:${f.line}) and surrounding context — do not trust the report. Is it real or a false positive (server-controlled input, upstream gate, test_mode gate, parameterized after all, unreachable, consumer is Claude not a naive interpolator)? Is it ACTUALLY exploitable — name the concrete attacker, the injection vector, and the outcome — or only theoretical/defense-in-depth? Default to skepticism: if you cannot construct a concrete exploit, mark exploitable=false and lower severity. Be honest if it holds.\n\nFINDING\nTitle: ${f.title}\nSeverity: ${f.severity} (confidence ${f.confidence})\nCategory: ${f.category}\nFile: ${f.file}:${f.line}\nDescription: ${f.description}\nClaimed exploitability: ${f.exploitability}\nEvidence: ${f.evidence}\n\nReturn your verdict via the schema.`
}

const ACTIVE = (args && Array.isArray(args.focus) && args.focus.length)
  ? DIMENSIONS.filter((d) => args.focus.includes(d.key))
  : DIMENSIONS

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
log(`KB audit: ${allVerified.length} raw, ${confirmed.length} confirmed, ${falsePositives.length} false positives across ${byDim.length} dimensions`)

phase('Critic')
const confirmedDigest = confirmed.map((f) => `[${f.verdict.revised_severity}] ${f.dimension}: ${f.title} (${f.file}:${f.line})`).join('\n')
const critic = await agent(
  `${PREAMBLE}\n\nYou are the COMPLETENESS CRITIC for the KB subsystem. Confirmed findings so far:\n${confirmedDigest || '(none)'}\n\nFind what was MISSED across the whole KB attack surface: an ingest source or a code path no dimension owned; the end-to-end indirect-prompt-injection chain from a scraped newsfeed article to an assistant/Claude-Code ACTION; a secret/PII path from journal/incident content into a search result that skips scrubSecrets; a test_mode-gate bypass; an FTS5/vector injection nuance; a prune-the-corpus bug; MCP read-only bypass. Report NEW findings only (structured schema); do not repeat confirmed ones. If coverage is complete, say so in areas_checked_clean and return empty findings.`,
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
  generated_for: 'radon-knowledge-security-audit',
  dimensions_run: ACTIVE.map((d) => d.key),
  confirmed: confirmed.concat(criticConfirmed),
  false_positives: falsePositives.map((f) => ({ title: f.title, file: f.file, dimension: f.dimension, reason: f.verdict ? f.verdict.justification : 'verifier error' })),
  clean_areas: byDim.map((b) => ({ dimension: b.dimension, summary: b.areas_clean })),
  critic_clean: critic ? critic.areas_checked_clean : null,
  counts: { raw: allVerified.length, confirmed: confirmed.length + criticConfirmed.length, false_positives: falsePositives.length, critic_new: criticConfirmed.length },
}
