# How the Radon harness and a Radon-tailored small model tie together

Date: 2026-09-12
Companion reports: [`radon-harness.md`](radon-harness.md) (the harness) and [`radon-small-model.md`](radon-small-model.md) (training a small model). This document does not repeat them; it says where they meet, which one comes first, and what a single roadmap looks like.

## Executive summary

The two reports were written independently and arrived at the same seam. The harness report's core deliverable is a hook pipeline that runs the four gates as code on every proposal and a session store that persists every turn, tool call, gate verdict and proposal as a replayable JSONL tree. The training report's core deliverable is a dataset of decision traces with deterministic gate labels, templated facts, and a time-split held-out evaluation. Those are the same artifact seen from two sides. The harness produces, as a by-product of running, exactly the traces the model needs; the model, once it exists, plugs into the harness as one more provider behind `web/lib/llm/provider.ts` and is called as a narrow tool, never as the decision surface. The evaluation suites are shared: evaluate replays, NO_TRADE gate matches, golden-set hit@5 and synthetic gate cases score both "is the harness better than the chat UI" and "is the small model tailored to Radon", with the harness as the eval runner and the model as one variable.

So the order is settled by dependency, not preference: harness extraction and session persistence first, because until sessions are persisted there is no trace source beyond what already sits in the journal and reports; the training report's Phase 0 (dataset from existing artifacts) and Phase 1 (a narrow tagger and drafter) in parallel, because they need nothing from the harness; decision-trace SFT last, because it needs months of persisted harness sessions to be worth running. The end state is one loop, four gates in code, one session store, one eval directory, and a provider list that includes a local model the harness can reach for when the task is formatting, tagging or drafting.

## 1. Where the two reports agree, in their own words

| Theme | Harness report | Training report | Consequence |
|---|---|---|---|
| The frontier model plus retrieval is the decision surface | "Order placement stays exactly where it is today: a proposal the harness cannot execute" and the model "fills the typed proposal; the harness's job is to say which gate failed" | "The frontier model plus RAG stays the decision surface; the small model formats and pre-fills" | No surface, in either plan, lets a small model reach `mutate.trading`. That is one pin test, not two. |
| Daily facts belong in retrieval, not weights or prompts | Context assembly is "just-in-time retrieval" against an attention budget; the knowledge MCP is "LLM-free retrieval primitives" | "Knowledge that changes hourly or daily belongs in retrieval and tools, not weights"; the KB already runs hourly at hit@5 0.917 | The harness's `tool_result` hooks and the training set's fact templating are the same discipline: a fact the model can retrieve at inference time is never baked in. |
| Gates are code, and code produces the labels | `convexity_gate`, `kelly_gate`, `order_limits`, `trading_halt` inside a `tool_call` block hook, "a failing gate returns `{ block: true, reason: "GATE 1 convexity: 1.4x < 2x" }`" | "The gates are enforced in code; the model's job is to invoke and narrate, not to be the calculator. Score naming accuracy, not arithmetic." | The gate verdicts the harness writes into every session are the ground-truth labels for the model's gate-compliance metric. No human labelling needed. |
| Today's transcript store is too thin | "Radon records one `assistant_turns` row per turn but does not persist the message array, tool inputs, or tool outputs ... a proposal cannot be replayed" | "`assistant_turns` keeps only 200 characters of the user message ... not usable as traces today" | Both reports independently name the same gap; closing it (harness Phase 1) is the single highest-leverage step for both goals. |
| Provider swappability is already built | `provider.ts` "normalizes xAI, Anthropic, OpenAI-compatible and Gemini behind one `chat()`"; Pi's `streamFn` is the model | "`provider.ts` already routes Ollama, so a local model can be plugged in without new plumbing" | A fine-tuned Qwen or Gemma adapter served by Ollama is one entry in the same provider list the nightly ladder uses. |
| Evals from Radon's own history, not synthetic prompts | Evaluate replays, NO_TRADE decisions, proposal gating, golden set, incident reasoning, loop phases, determinism | Format adherence, gate compliance, hallucination probes, held-out decision agreement, and P&L only through the backtest harness | One `evals/` directory; the harness runs it; the model under test is a parameter. |
| The nightly loops are a harness written in bash | "The five wrappers' phase contract ... is harness policy written in bash. It should become a headless driver of the same core." | The continual pipeline is "nightly trace_builder ... weekly eval_suite ... monthly LoRA" | The trace builder, the eval run and the adapter promotion are three more nightly-loop phases with the same dead-man reporting the loops already have. |

## 2. Where they pull in different directions, and how to resolve it

**Pi as dependency versus reference.** The harness report leans toward adopting Pi's design and optionally its `pi-agent-core` package. The training report needs nothing from Pi. Resolution: adopt the design, not the package, for now. The one thing Radon must own outright is the session entry schema, because the training pipeline reads it; a schema owned by a third-party package would put the dataset format one upstream release away from drift. Revisit the package question after Phase 1 when the schema is pinned by a test.

**Where the Claude Agent SDK fits.** The harness report reserves it for the nightly engineering loops, not the trading agent. The training report notes Anthropic's output-training clause permits narrow tasks (tagging, extraction, summarization) and restricts a competing general model. These agree: the SDK-driven loops can keep producing engineering PRs, and their transcripts are not a training source. The trading harness's sessions are, and they are narrow by construction.

**Cost.** The harness report worries about moving the loops onto API billing; the training report says the local model does not pay for itself in API spend. Both are right and neither changes the plan: the loops stay on the codex/grok ladder, the small model is justified by privacy, latency and offline fallback, and the harness's per-session `max_budget_usd` is what actually bounds spend.

**What "constantly studying" means.** The operator's framing (the model itself becomes tailored) is closest to the training report's category (ii), skills and house rules. The harness report's context assembler and compaction summary are where categories (i) and (iii) live: positions, flow and news are re-injected per turn from retrieval; signal edge stays in the backtest harness. The honest version of "constantly studying" is: the harness re-reads Radon every turn, the small model re-learns Radon's habits monthly from what the harness recorded, and neither ever learns a regime into its weights.

## 3. The shared artifact: a session entry that is also a training example

The harness report proposes JSONL entries with `id` and `parentId`; the training report proposes traces in TRL and mlx-lm conversational form with templated facts. Design the session entry so the trace is a projection of it, not a second pipeline:

```
session entry (harness writes)                trace (trace_builder reads)
-------------------------------               -----------------------------
id, parentId, ts, session_id                  split key = ts (time-split only)
role: user | assistant | tool_use |           messages[] in chat form
      tool_result | proposal | gate
tool_use: name, capability, input             kept if capability is read.*
tool_result: name, bytes, truncated,          replaced by a placeholder token when
             untrusted, content               the fact is retrievable at inference
proposal: structure, legs, max_gain,          assistant target: thesis, gates named,
          max_loss, kelly_fraction, signal    risk factors, exit and stop
gate: name, verdict, reason                   label for gate-compliance scoring
provider, model, usage, cost                  provenance filter (never train on a
                                              trace produced by the adapter itself)
```

Three rules fall out of this, and both reports already state them separately:

1. Facts that are retrievable at inference time are templated before they reach the training set (`NVDA at 182` becomes `{{last_price}}`), so the model learns the reasoning shape and not a stale number. The harness's `untrusted` flag on tool results doubles as the "do not memorize" flag.
2. Entries produced by the small model itself carry its provider and model name and are excluded from its own next training set. Self-distillation loops are how a narrow model drifts.
3. The time split is the only split. The harness records `ts`; the eval holds out the last 60 to 90 days; the nightly backtest lane already imposes the same walk-forward rule on signal models.

## 4. One roadmap, with the dependency edges made explicit

The phases below reuse the numbering of the two companion reports (H = harness, M = model) so each line traces back to its source.

| Order | Phase | From | Depends on | Produces | Weeks |
|---|---|---|---|---|---|
| 1 | H0: extract `loop.ts`, `tools.ts`, `dispatch.ts`, catalog, capabilities into `harness/`; assistant route becomes an adapter; 25 assistant tests pass unchanged | Harness §4.3 | nothing | a loop that is a library | 1 to 2 |
| 1 (parallel) | M0: `traces.py` from journal, reports and posts; curate the golden set out of `draft: true`; audit 200 newsfeed tags by hand; 200 synthetic gate cases; record the frontier plus RAG baseline | Model §5 | nothing (uses artifacts that exist today) | `eval_suite.py`, the baseline row | 1 (calendar), 1 to 3 days of work |
| 1 (parallel) | M1: local tagger on Qwen3.5-0.8B or 2B via mlx-lm, served by Ollama behind `provider.ts`; drafter on Qwen3.5-4B | Model §5 | M0 for the acceptance test | a provider entry the harness can call | 1 |
| 2 | H1: session store (JSONL tree on disk, mirrored to a new Turso `assistant_sessions` table), hook pipeline, compaction, budgets | Harness §4.3 | H0; the entry schema from §3 above | replayable sessions, the trace source | 2 to 3 |
| 3 | H2: gates as code in the `tool_call` hook; typed registry with `capability`, `readOnly`, `timeoutMs`; structured `signal` object on every proposal; wire tests that a blocked proposal produced zero `/api/orders/place` requests | Harness §4.3 | H1 | gate verdicts in every session; the model's gate labels | 2 |
| 3 (parallel) | H3 and M4 merged: one `evals/` directory; evaluate replays, NO_TRADE matches, golden set, gate cases, hallucination probes; the harness is the runner, the model is a parameter; determinism reported separately from accuracy | Harness §5, Model §4 | H1, M0 | one scoreboard for both questions | 2, then ongoing |
| 4 | H4: provider ladder as harness config; nightly loops become headless drivers (`modes/rpc.ts`); Claude rungs via the Agent SDK with `dontAsk`, `disallowedTools` and `PreToolUse` rails; codex and grok via the portable prompts | Harness §4.3 | H1, H2 | wrappers shrink to process supervision | 1 to 2 |
| 5 | M2: decision-trace SFT on accumulated harness sessions, retrained from scratch monthly on the full set with a replay slice, time-split hold-out, promotion rule (format, gates, hallucination not worse; agreement not worse by more than one standard error), rollback by symlink | Model §3 and §5 | H1 sessions accumulated for at least 60 to 90 days beyond the hold-out; H2 gate labels | a monthly adapter the harness may call as a draft tool | 2 to 3, then monthly |
| always | M3: signal edge stays in `backtest_runs`, Chronos-2, deflated Sharpe, purged CV; any LLM-derived feature entering a backtest comes from a model whose cutoff predates the window | Model §5 | independent | unchanged lane | ongoing |

Reading the table: nothing in the model track blocks the harness track, and only M2 waits on the harness. The first month delivers a harness that is a library, a dataset, a baseline number, and a local tagger. The third month delivers gates in code and a shared scoreboard. Decision-trace training does not start before month four, and it should not, because before then the traces it would learn from are the pre-harness ones the training report already sized at 150 to 300.

## 5. Definition of done for the combined effort

- Every proposal the assistant renders passed `convexity_gate`, `kelly_gate`, `order_limits` and `trading_halt` inside the harness, and a wire test proves a failing gate produced no `/api/orders/place` request.
- Every session is replayable from Turso alone, with gate verdicts and provider provenance on each entry.
- `evals/run` produces one JSON scoreboard with the frontier plus RAG baseline, the current adapter and the candidate adapter side by side, and the nightly loop that runs it posts the same dead-man comment the other loops post.
- The small model is reachable only through the harness's provider list, only for `read.*` capability tools, and a pin test greps the harness for any path that hands a `mutate.trading` tool to a non-frontier provider.
- The trace builder excludes entries the adapter produced, templates retrievable facts, and splits by time.
- No claim of edge is made from the LLM lane; P&L questions route to the backtest lane.

## 6. Decisions only the operator can make

Deduplicated from both reports' open questions, ordered by how early they block.

1. **Session persistence and real account figures.** Storing full transcripts is the enabling step for both tracks, and transcripts contain real positions and P&L. The R-454 and R-457 provenance rules apply; decide whether the Turso mirror stores the full entry or a redacted projection with the full copy on disk only. (Blocks H1 and M2.)
2. **One gate implementation or two.** Python `gates.py` behind `POST /gates/evaluate`, or a TypeScript twin with a parity test. One implementation keeps the labels the model trains on identical to the ones the harness enforces; a twin saves a hop per proposal. (Blocks H2.)
3. **Mac Mini memory.** 16, 24, 32 or 64 GB decides 4B LoRA versus 9B LoRA versus 12B QLoRA. (Sizes M1 and M2.)
4. **Is privacy alone enough to justify a local model**, given the API saving is under $100 a year? If not, M1 is still worth running once as a measured experiment and then parking. (Gates M1.)
5. **Nightly-loop spend model.** Claude subscription for security, codex/grok for the rest is the current rule; keeping it means the Agent SDK is only ever used for the security loop. (Shapes H4.)
6. **Pi as a dependency** after the session schema is pinned, or Radon-owned loop with Pi as reference permanently. (Revisit after H1.)
7. **Fate of the legacy `/api/pi` command route** once the harness's typed tools cover `scanner`, `evaluate` and `sync`. (H2 or later.)
8. **Gate 2 shape.** Model-assisted with a required structured `signal` object, or a numeric strength threshold enforced by the hook as well. (H2.)

## Sources

This document cites only its two companion reports; every external reference is in their source lists.

- [`radon-harness.md`](radon-harness.md), sections 1 to 5 and the open questions
- [`radon-small-model.md`](radon-small-model.md), sections 1 to 5 and the open questions
- Radon code referenced by both: `web/lib/assistant/{loop,tools,dispatch,catalog,capabilities}.ts`, `web/lib/llm/provider.ts`, `scripts/workflow/gates.py`, `scripts/order_limits.py`, `scripts/trading_halt.py`, `scripts/knowledge/{ingest,retrieve,eval_golden,mcp_server}.py`, `scripts/db/migrations/{0030,0065}_assistant_turns*.sql`, `docs/operations.md` (nightly loops)
