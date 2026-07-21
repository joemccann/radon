# Radon Knowledge: marketing source document

Status: DRAFT for the radon.run marketing site. Written while Phase 1 was in flight.
Do not publish until the implementation status table at the bottom is all green and
every claim has been re-verified against the shipped system. Follows brand voice
(docs/brand-identity.md section 8): precise, calm, scientific, unsensational.
House rule: no em dashes in any user-facing copy. None appear below.

---

## Positioning

**One line.** Radon remembers every trade, every thesis, every incident, and every
signal it has ever processed, and answers questions about them in plain language.

**The problem.** A trading operation produces two kinds of output: positions and
exhaust. The exhaust is where the judgment lives: the thesis behind an entry, the
eval that rejected a trade, the incident report from the night the data feed
degraded, the lesson recorded after a bad fill. In most operations that exhaust is
scattered across journals, reports, chat logs, and memory. Six months later the
question "why did I enter this position" has no queryable answer.

**The answer.** Radon Knowledge is a retrieval layer built into the terminal. It
continuously ingests the operation's own records: the trade journal, structured
trade evaluations, market research feeds, methodology documents, and operational
incident history. Everything lands in one queryable store. The operator, the
built-in assistant, and external agents all ask it questions through the same
interface.

**Why it is credible.** The design follows the same architecture that large
engineering organizations use for internal knowledge systems: a single shared
embeddings table, plugin connectors per source, and hybrid retrieval that fuses
lexical, semantic, and recency signals. Radon applies that pattern to a domain
where the corpus is smaller but the stakes per answer are higher.

---

## Hero copy options

Option A (declarative):
> **Your operation, on the record.**
> Radon Knowledge turns the exhaust of trading into a queryable memory: theses,
> evals, incidents, research, and lessons, retrievable by meaning.

Option B (question-led):
> **"Why did I enter this position?"**
> Radon answers from your own records. Every thesis, eval, incident, and lesson
> the system has processed is retrievable in one query.

Option C (systems):
> **Institutional memory, reconstructed.**
> One store. Five sources. Three retrieval signals. Every answer cites the
> original record.

Deck line under any option:
> Built on the same knowledge-base architecture used inside large engineering
> organizations, scaled to the data that matters: yours.

---

## Feature sections

### 1. One store, every source

Trade journal fills. Written theses. Seven-milestone trade evaluations. Tagged
market research from the newsfeed. Methodology documents. Operational incident
digests. Each source has its own connector; every record lands in the same
store with the same shape. Adding a source is writing one small module, not
building a system.

### 2. Retrieval that does not trust a single signal

Keyword search catches what embeddings blur: tickers, error strings, strike
prices. Vector search catches paraphrase: the question and the answer rarely
share vocabulary. Recency weighting encodes that a research note expires but a
structure definition does not. No single scorer is trusted alone. Results from
each signal are fused, deduplicated, capped per source, and returned with
surrounding context restored.

### 3. Answers cite the record

Distilled summaries are retrieval keys, never the evidence. Every answer links
back to the raw record: the journal row, the eval report, the incident digest.
The system is designed so a wrong summary cannot silently become a wrong answer.

### 4. Private by construction

Journal rows carry real positions and real profit and loss. Radon embeds them
locally, on the operator's own hardware. Position data never transits a
third-party embeddings service. Retrieval runs inside the same authenticated
perimeter as the rest of the terminal.

### 5. Three surfaces, one memory

The assistant panel answers questions in the terminal. The evaluation pipeline
pulls prior theses and lessons before a new trade is scored. Agent tooling
exposes the same retrieval primitives over MCP, so external agents and coding
sessions query the operation's memory directly. Same store, same evidence,
three consumers.

### 6. Measured before trusted

Retrieval quality is tested against a curated set of real operator questions
with known correct answers. The assistant integration ships only when the
benchmark passes. Search that cannot prove itself does not reach the operator.

---

## How it works (technical sidebar)

For the reader who wants the mechanism. Keep this section compact on the site.

1. **Ingest.** Connectors run on a timer. Each emits its current documents;
   unchanged documents are skipped by content hash, changed ones are re-indexed,
   vanished ones are pruned.
2. **Distill.** A fast language model normalizes each document into a searchable
   form: the one-line question an operator would actually type, plus a compact
   summary and extracted tickers. Raw text is keyword-searchable immediately.
3. **Embed.** A local embedding model encodes the distilled form into a 384
   dimension vector. No document content leaves the machine for embedding.
4. **Retrieve.** A query runs three legs at once: full-text match with BM25
   ranking, vector similarity over an ANN index, and per-source recency decay.
   Reciprocal rank fusion merges the legs. Results are deduplicated per document,
   capped per source, and expanded with neighboring context.
5. **Answer.** The assistant synthesizes a response from the evidence packet and
   cites the underlying records.

Stack notes for the credibility-minded: single shared table in the terminal's
existing database, FTS5 for lexical search, native vector search with a
pre-built ANN index, connectors as plain modules, and a golden-set evaluation
harness gating every retrieval change.

---

## Stat callouts

Fill with real measured values at publish time. Do not publish placeholders.

| Callout | Value | Source of truth |
|---|---|---|
| Sources ingested | 5 (journal, evals, docs, newsfeed, incidents) | connector registry |
| Documents indexed | 4,933 at 2026-07-20; re-query at publish | `SELECT COUNT(*) FROM knowledge` |
| Retrieval legs fused per query | 3 (lexical, vector, recency) | retrieve.py |
| Golden-set hit rate | 0.917 hit@5 measured 2026-07-19 (gate was 0.8); re-run at publish | eval_golden.py output |
| Median query latency | TBD, measure end to end | to be measured |
| Third-party services touching position data | 0 for embedding and storage | architecture |

---

## FAQ draft

**What data does it index?**
The operation's own records: trade journal and fills, written theses, trade
evaluations, methodology documents, operational incident digests, and the tagged
market research feed. Nothing external, nothing scraped from other users.

**Does my trading data leave the system?**
Embeddings are computed locally and stored in the terminal's own database.
Distillation summarizes documents through a language-model API; raw position
figures are not required for that step and the store keeps raw text private
behind the terminal's authentication. See the privacy section of the docs for
the exact data path.

**How current is it?**
Connectors run on an hourly cycle. A journal fill or newsfeed post is queryable
within the hour; keyword search over raw text is available as soon as a record
is ingested.

**Can I add my own source?**
Yes. A connector is one module that emits documents in the shared shape. If it
lands in the store, it is retrievable through every surface with no other
changes.

**How do I know the answers are right?**
Every answer cites its records, and retrieval quality is benchmarked against a
curated question set before any surface ships. When evidence is thin, the
assistant says so.

---

## Publish checklist (site team)

- [ ] Phase 2 (assistant integration) shipped and golden-set gate passed
- [ ] Replace every TBD in stat callouts with measured values
- [ ] Verify the privacy claims against the shipped distillation data path,
      including exactly what text is sent to the distillation API
- [ ] Screenshots: assistant answering a thesis question with citations, and
      the eval pipeline prior-context block (anonymize per site PLATE rules,
      no real account figures)
- [ ] Cross-link: docs page for the connector contract if we want a
      build-your-own-connector story
- [ ] Voice pass against brand-identity.md section 8, confirm no em dashes,
      no hype adjectives, no emojis

## Implementation status (update before publish)

| Piece | Status (updated 2026-07-21) |
|---|---|
| Store, hybrid retrieval, migrations (Phase 0) | Shipped |
| Connectors, distillation, embeddings, timer (Phase 1) | Shipped; hourly ingest self-converging on the VPS |
| Golden-set harness | Shipped; 0.917 hit@5, question set still operator-uncurated |
| Assistant tools (Phase 2) | Shipped; verified live with cited answers |
| MCP server (Phase 3) | Shipped; registered as radon-kb for agent sessions |
| Resilience + security hardening | Shipped (retry/logging/ranking; MCP filter caps, PII scrub) |
