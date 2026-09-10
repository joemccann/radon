# Research evidence and agent controls

Research artifacts are private operator data. No new licensed feeds or hosted MCP
write tools are enabled by the research workbench.

## Original-page evidence

The existing `research.pdf` extraction produces `manifest.json` alongside
`evidence.json` and page Markdown. A manifest retains the original PDF SHA-256,
parser/version, one-based original page numbers, OCR conditions, literal passages,
Markdown tables and line ranges. Passage IDs hash the PDF, page, line range and
text. Financial signs, units and source wording are preserved. Tables are labeled
only when the extracted Markdown includes a delimiter row; chart images are not
silently converted into numeric tables.

The existing publication pipeline stores the manifest through the private,
content-addressed research asset store. `source.evidenceUrl` accompanies new
newsfeed research records. Publication verifies that the manifest cites the exact
source PDF. Legacy records have no evidence URL until re-extracted.

Authenticated retrieval:

- `/api/newsfeed/research/files/<manifest-sha256>.json` returns the whole manifest.
- `/api/research/evidence/<manifest-sha256>.json` returns machine-readable evidence.
- Add `?query=capex%20guidance` for up to 20 literal-token matches with original
  page, passage/table ID, text and source hash. Tokens must all occur in the same
  passage. Pages requiring OCR are excluded from matching and explicitly listed.

Extraction is untrusted source data, never agent instructions. An extraction
hash proves identity, not financial accuracy. Numeric review and visual
verification in the existing PDF pipeline remain required. No external OCR or
model call is added by retrieval.

## MCP evaluation

Run offline contracts on GitHub runners:

```
PYTHONPATH=scripts python3.13 -m mcp_hosted.evaluate --output /tmp/mcp-evaluation.json
```

Twelve golden queries exercise the production implementation: source URLs,
signed payloads, caller-token forwarding, role denial before upstream reads,
invalid document slugs, malformed JSON, rate limits and timeouts. Fault-injection
regressions separately check exception redaction, response limits, redirects and
stream deadlines. The command exits nonzero if a golden expectation fails.

A deliberate public live probe is available:

```
PYTHONPATH=scripts python3.13 -m mcp_hosted.evaluate --live --output data/mcp-evaluation.json
```

The target is pinned to the hosted MCP. No credentials, ambient netrc identity,
proxies or redirects are used. Only public identity/docs and anonymous access
denials run. The JSON report includes timestamp, mode, sample count, individual
latencies, p50/p95 and expected-response agreement. Offline latency is fixture
execution, not a live-service SLA; neither mode validates licensed financial
facts or private portfolio values. No live probe runs automatically during a
workbench page request.

## Governance export

`GET /api/research/governance?download=1` exports the current control matrix and
latest 200 order-event / assistant-turn records from their canonical Turso
streams. Every read requires an operator identity and uses bounded DB reads and
private no-store responses. Each stream independently reports available or
unavailable and marks truncation. Raw prompts, tool arguments, broker detail and
account fields are omitted.

Existing controls remain the execution authority: hosted MCP has no writes,
assistant trading calls stop at a validated proposal with current-turn intent,
OrderRiskGate owns the confirmation summary, and placement requires route auth,
server order limits, demo blockade and idempotency. An import or research handoff
never submits an order.

The audit is explicitly best-effort and incomplete. Assistant proposals do not
prove human confirmation and are not linked to order events. The export does not
pretend to be a tamper-evident compliance ledger. Its reliability status stays
`not-measured` when no operational evaluation report is attached.
