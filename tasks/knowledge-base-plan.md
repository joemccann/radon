# Radon Knowledge Base — Plan

> **STATUS (2026-07-20): Phases 0–3 SHIPPED.** Corpus: 4,933 docs across all
> five sources, hourly self-converging ingest on the VPS, golden-set hit@5
> **0.917** (gate was 0.8). Assistant answers with doc_key citations in
> production; `radon-kb` MCP server registered in `.mcp.json` for Claude Code.
> Commits: Phase 0 `e10061b7`, Phase 1 `f3dee350` (+ `359f8120`, `1fcd25af`,
> `ac883c84` hardening), Phase 2 `e61d9dd2`, Phase 3 `ed908a0c`.
> Per-phase outcome notes are inline below. Phase 4 remains evidence-gated
> and unbuilt. Operator follow-ups: curate `scripts/knowledge/golden_set.json`
> (still `draft: true`), decide on indexing `web/CLAUDE.md`-class files (one
> golden miss lives there), marketing doc publish gates
> (`tasks/artifacts/knowledge-base-marketing.md`).

Source pattern: [How Cerebras Built Its Enterprise Knowledge Base](https://www.cerebras.ai/blog/how-we-built-our-knowledge-base) (read 2026-07-18).

## What Cerebras built (compressed)

- One Postgres table holding embeddings + raw summaries + metadata from every source (Slack, wiki, code, custom DBs). Connectors are plugin scripts that emit rows in that shared schema.
- Hybrid retrieval, no single scorer trusted: lexical full-text (GIN/BM25) for exact tokens, vector similarity for paraphrase, IDF weighting to kill filler, recency decay because answers expire. Lists fused with reciprocal rank fusion (RRF), deduped per source, LLM-reranked to top 10, then re-expanded with neighboring context.
- Slack ingestion: socket-mode bot, thread-level re-fetch on every reply, LLM "distillation" of each thread into a normalized searchable form (one-line question, systems mentioned). Raw text is keyword-searchable immediately; only distilled forms are embedded. "Bursts" (consecutive same-author runs) embedded separately when they clear an IDF/length/reactions threshold.
- Code embeddings via CocoIndex (incremental re-embedding of changed chunks; sync state in the same Postgres).
- Query side: planner (small LLM picks tools) → parallel executor → synthesis LLM. Same primitives exposed raw over MCP so agents (Claude Code) do their own orchestration — tools are deliberately LLM-free.
- "Projects" scope queries to a named bundle of sources so search is relevant by default.

## The honest counterargument first

Cerebras built this for a company answering 15,000 questions/day across hundreds of employees. Radon is a one-operator system with a ~5k-document corpus (819 journal rows, 3,474 newsfeed posts, 183 eval/report files, 86 scan snapshots, 31 docs, lessons/memory). Most of their machinery solves organizational-scale problems Radon does not have. Copying it wholesale would violate Radon's own simplicity-first rule.

What Radon **does** have is the same disease in miniature: valuable exhaust scattered across Turso tables, HTML reports, markdown docs, and lessons files, none of it queryable by meaning. "What was my thesis on the EWY risk reversal?", "when did COR1M last print above 60 and what did CRI do?", "what fixed the 2026-07-02 destroy-storm?" are all grep-archaeology today — for the operator, for the assistant panel, and for Claude Code sessions.

So the plan is a **thin slice**: the shared-embeddings-table + hybrid-retrieval + LLM-free-MCP-primitives core, and an explicit skip list for the rest.

## What transfers / what gets skipped

**Transfers:**
- Single shared embeddings table in the existing canonical DB (Turso, not a new Postgres) — their "sync state and embedding store in the same database" advantage, for free.
- Plugin connector contract per source, distillation into a normalized searchable form before embedding.
- Hybrid retrieval: FTS5 BM25 + vector cosine + recency decay, fused with RRF; per-source dedup; neighbor-chunk context expansion.
- Planner→executor→synthesis exists already: `web/lib/assistant/loop.ts` is a working Anthropic tool loop with 5 tools. The KB plugs in as new tools, not a new agent.
- MCP primitives for Claude Code, deliberately LLM-free.
- Scopes ("projects" analog): `trading` / `research` / `ops` source classes.

**Skipped (deliberately):**
- Socket-mode realtime ingest — systemd timers are the house pattern and 30–60 min freshness is fine.
- Bursting — no Slack-like corpus; newsfeed posts and journal rows are already document-shaped.
- Dedicated reranker model — at ~5k docs, RRF + recency is enough; revisit only if golden-set evals say otherwise.
- Code embeddings / CocoIndex — Claude Code + grep + Serena already cover a repo this size; Cursor's findings apply to 40 GB monorepos.
- Per-user projects, onboarding, expertise-finding — one operator.

## Architecture

```
 CONNECTORS (plugin modules, one systemd timer ~30-60min, service_health heartbeat)
 ┌─────────────┬──────────────┬─────────────┬──────────────┬─────────────┐
 │ journal     │ eval reports │ newsfeed    │ docs+lessons │ incidents    │
 │ (Turso)     │ (reports/*)  │ (posts tbl) │ (*.md)       │ (svc_health/ │
 │             │ HTML→text    │             │              │  watchdog)   │
 └──────┬──────┴──────┬───────┴──────┬──────┴──────┬───────┴──────┬──────┘
        │  distill (Cerebras API, normalized summary + question form)
        │  embed (local ONNX, bge-small-en-v1.5, 384d — data never leaves)
        ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ Turso `knowledge` table                                          │
 │ id · source · scope · doc_key · chunk_ix · title · summary       │
 │ content · metadata JSON · embedding F32_BLOB(384) · content_hash │
 │ created_at · last_activity_at                                    │
 │ + FTS5 mirror (content, summary, title)                          │
 └──────────────────────────────────────────────────────────────────┘
        ▲                    ▲                     ▲
        │ SQL (HTTP pipeline; vector fns evaluate server-side)
 ┌──────┴──────┐      ┌──────┴───────┐      ┌──────┴────────┐
 │ FastAPI     │      │ Assistant    │      │ MCP server    │
 │ /knowledge/ │◄─────│ tools (⌘J    │      │ (Claude Code) │
 │ search      │      │ loop.ts)     │      │ kb_search,    │
 │ (auth'd)    │      │ search_      │      │ kb_prior_evals│
 └─────────────┘      │ knowledge,   │      │ kb_incidents  │
                      │ prior_evals  │      └───────────────┘
                      └──────────────┘
 Retrieval per query: FTS5 BM25 ∥ vector cosine ∥ recency
                      → RRF fuse (k=60) → dedup per doc_key
                      → cap per source → expand neighbor chunks
```

Key decisions:
- **Storage: Turso, not Postgres/pgvector.** libsql supports `F32_BLOB`, `vector_distance_cos`, `vector_top_k`, and FTS5; SQL evaluates server-side over the HTTP pipeline, so the pinned `libsql_experimental` client version doesn't gate vector functions. Zero new stateful services. At 384d × ~10k rows the table adds ~15–30 MB. (Phase 0 includes a verification spike on the current Turso plan; risk noted below.)
- **Embeddings: local ONNX (fastembed / bge-small-en-v1.5, 384d).** Privacy is the driver — journal rows carry real positions and P&L; they should not transit a new third-party embeddings API. CPU inference on laptop/VPS is trivial at this corpus size, marginal cost zero. Env-var escape hatch to a hosted model if quality disappoints.
- **Distillation: Cerebras API.** `CEREBRAS_API_KEY` is already provisioned in `web/.env` and currently unused. OpenAI-compatible chat endpoint via `requests` — no SDK. Fits the "cheaper models for grunt work" rule. Only summaries/question-forms are distilled; raw text is FTS-searchable immediately (their pattern).
- **Recency decay is per-source:** newsfeed and incidents decay; docs, options-structures taxonomy, and methodology do not ("Slack answers expire" — but a defined-risk structure definition doesn't).
- **Demo isolation:** knowledge endpoints ride the existing operator allowlist / auth perimeter; the demo instance (TEST_MODE) gets no KB — reports contain real account figures.

## Pros

1. **One queryable memory over all of Radon's exhaust.** Prior theses, evals, incidents, lessons, and news become answerable in the ⌘J assistant and in Claude Code, replacing grep-archaeology.
2. **Near-zero new infrastructure.** Same Turso DB, same FastAPI, same systemd-timer + service_health patterns, same auth perimeter, existing assistant loop. The only genuinely new runtime piece is a ~50 MB ONNX embedding model.
3. **Directly feeds the evaluate pipeline.** A `prior_context` step (prior evals of the ticker, similar structures, relevant lessons) grounds Milestone 4 edge decisions in Radon's own history.
4. **Claude Code sessions get institutional memory.** MCP primitives mean repo sessions can ask "what fixed the relay farm-down?" instead of re-deriving it — compounding value for every future session.
5. **Cheap.** Distillation of the full backlog is a few dollars of Cerebras tokens once; incremental cost is cents/day. Embeddings are local.
6. **Incremental by construction.** `content_hash` idempotency gives CocoIndex-style re-embed-only-what-changed without the framework.

## Cons / risks

1. **Over-engineering pull.** The article's gravitational field is real; the skip list above is the defense. If a phase isn't answering actual operator questions, stop.
2. **Turso vector/FTS5 maturity.** Less battle-tested than pgvector; the Phase 0 spike must confirm both work on the current plan before anything else is built. Fallback: FTS5-only + Python-side cosine over a candidate window (fine at this scale), or worst-case a local SQLite index file (violates Turso-first; last resort).
3. **New Python deps on the fleet.** `fastembed`/`onnxruntime` must be pinned per the VPS pip-freeze discipline; onnxruntime wheels are large and occasionally platform-fussy on deploy.
4. **Retrieval quality is unproven until evaluated.** Bad retrieval silently poisons assistant answers. Mitigation: a 20–30 question golden set from real operator questions, hit@5 measured before the assistant tool ships (their "we tested raw embeddings and they weren't enough" lesson, applied in advance).
5. **Another writer to babysit.** New timer + service_health row + staleness window (24h, event-driven-writer rule). Small but nonzero ops surface.
6. **Distillation drift.** LLM-normalized summaries can subtly misstate a thesis. Mitigation: store raw content alongside; the assistant cites and quotes raw text, summaries are retrieval keys only.
7. **Corpus is small enough that grep sometimes wins.** For exact-token queries (error strings, tickers) FTS alone would cover much of the value; the vector leg earns its keep only on paraphrase ("restore hangs" vs "checkpoint stalls"). That's an argument for shipping FTS first and measuring.

## Implementation plan

**Phase 0 — Spike + schema (~0.5 day)** — ✅ SHIPPED `e10061b7`
- Verify on current Turso plan: `F32_BLOB` columns, `vector_distance_cos`, `vector_top_k`, FTS5 virtual tables via the HTTP pipeline. Abort criteria defined above.
- Migration `00XX_knowledge.sql`: `knowledge` table + FTS5 mirror + triggers.
- `scripts/knowledge/{schema.py,store.py,retrieve.py}`: row contract, idempotent upsert on `content_hash`, hybrid search (FTS5 BM25 ∥ vector ∥ recency → RRF → dedup → neighbor expansion).
- Unit tests with a fixture corpus (window-relative dates).
- *Outcome:* all Turso capabilities confirmed. Two deviations from plan: the
  ANN index is NOT backfilled by `CREATE INDEX` over existing rows, so
  migration `0028` creates it before any ingest (ordering is load-bearing);
  the FTS5 mirror is code-maintained rather than trigger-based because
  `migrate.py`'s statement splitter cannot carry multi-statement trigger
  bodies. Local `libsql_experimental` supports FTS5 + vector fns, so tests
  run on real `:memory:` DBs with no scorer stubs.

**Phase 1 — Connectors + backlog ingest (~1–2 days)** — ✅ SHIPPED `f3dee350` + hardening `359f8120`/`1fcd25af`/`ac883c84`
- Connector contract: `fetch() -> Iterable[KnowledgeDoc]` per source; plugin modules under `scripts/knowledge/sources/`.
- Sources in value order: journal (Turso rows + trade_log rationale), eval reports (`reports/*.html` → text via stdlib parser), docs + `tasks/lessons.md`, newsfeed posts, service_health incident digests.
- Distillation via Cerebras (`scripts/knowledge/distill.py`), local embeddings (`embed.py`).
- One-shot backfill script, then `radon-knowledge.timer` (30–60 min) + service_health heartbeat (`knowledge-ingest`, 24h staleness window).
- Golden-set eval harness (`scripts/knowledge/eval_golden.py`, 20–30 real questions, hit@5 report).
- *Outcome:* hourly timer at `:20` UTC, installed on the VPS via one-time
  root transaction (deploy.sh does not install units). The backfill surfaced
  four production lessons now encoded as tests: (1) host-local file sources
  (evals, trade_log) must NEVER get vanished-doc prune authority — a
  present-but-partial `reports/` on the VPS deleted 177 laptop docs; (2/3)
  every read of a large corpus must paginate on an id cursor — one unbounded
  SELECT of all posts, and later of all stored rows, each 502'd Turso's HTTP
  pipeline; (4) changed docs are distilled/embedded/written in 200-doc
  batches on fresh connections — one long-lived Hrana stream carrying idle
  distillation minutes plus ~9k writes 502s, and batching makes progress
  durable. Distillation runs on the VPS too (`CEREBRAS_API_KEY` was already
  in the fleet env).

**Phase 2 — Query surfaces (~0.5 day)** — ✅ SHIPPED `e61d9dd2` (gate passed at 0.917)
- FastAPI `GET/POST /knowledge/search` (authenticated, NOT auth-exempt; scope + source filters).
- Assistant tools in `web/lib/assistant/tools.ts`: `search_knowledge`, `find_prior_evals(ticker)` — both READ tools through the existing loop; Next.js proxies via `radonFetch`. System prompt nudge to consult prior context before theses.
- Gate: ships only if golden-set hit@5 ≥ 0.8.
- *Outcome:* retrieval runs entirely inside `asyncio.to_thread` (embedder
  cold load included); embedder-unavailable degrades to FTS-only; demo VM
  `test_mode` short-circuits to empty results so trial users never see the
  operator corpus; `compact=true` bounds payloads server-side; `embed.py`
  defaults `FASTEMBED_CACHE_PATH` so radon-api reuses the ingest unit's
  downloaded model. Verified live end to end: the assistant answered the EWY
  risk-reversal thesis question with doc_key citations and cross-source
  lessons (naked-put risk, 3 DTE vs the prior IWM eval's 14–60 DTE band).

**Phase 3 — MCP server (~0.5 day)** — ✅ SHIPPED `ed908a0c`
- `scripts/knowledge/mcp_server.py` (stdio, `mcp` package): `kb_search`, `kb_recent`, `kb_prior_evals`, `kb_incidents`. LLM-free, narrow structured I/O (their design).
- Register in `.mcp.json` for laptop Claude Code sessions.
- *Outcome:* side-effect-free imports and a SELECT-only guarantee are both
  test-pinned; output bounds mirror Phase 2. `hybrid_search` gained
  `with_neighbors=False` so `kb_search` skips neighbor-expansion SELECTs it
  never returned. Live stdio smoke verified clean JSON-RPC framing with zero
  stdout pollution against the real corpus.

**Phase 4 — Optional, evidence-gated**
- LLM rerank pass (Cerebras scores 0–10) only if golden set shows fusion ordering is the bottleneck.
- Scope-aware defaults per surface; evaluate-pipeline `prior_context` milestone hook.
- Newsfeed semantic search in the UI (today tag-filtered only).

## Dependencies

| Dependency | Status | Notes |
|---|---|---|
| Turso vector fns + FTS5 | verify in Phase 0 | server-side SQL; no client upgrade expected |
| `fastembed` + `onnxruntime` | new Python deps | pin per VPS pip freeze; ~50 MB model cached on disk |
| Cerebras API | key already in `web/.env`, unused | plain `requests`, OpenAI-compatible; no SDK |
| `mcp` (Python) | new dep, laptop-first | Phase 3 only |
| HTML→text for reports | stdlib `html.parser` | avoid adding bs4 |
| systemd timer + service_health | existing patterns | one new unit + heartbeat row |
| Anthropic (assistant loop) | existing | KB adds tools, no loop changes |
| No new stateful services | — | no Postgres, no vector SaaS, no queue |

## Explicit non-goals

Code embeddings, realtime ingest, burst embeddings, reranker-by-default, multi-user projects, expertise finding, a standalone "knowledge UI" (the ⌘J assistant and Claude Code are the UIs).
