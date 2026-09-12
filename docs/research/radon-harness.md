# A Radon-Specific Agent Harness: Landscape, Pi Study, and Recommendation

Date: 2026-09-12
Repo studied (read-only): `radon-security` clone at `.claude/worktrees/loops-session-limit`, HEAD `391aaaea`

## Executive summary

Radon already has most of a harness, it is just spread across four places that do not know about each other: a hand-written tool loop in `web/lib/assistant/` (17 typed tools, a capability catalog, a destructive-tool halt, per-turn telemetry), a set of Python gate and limit modules (`scripts/workflow/gates.py`, `scripts/order_limits.py`, `scripts/trading_halt.py`), a read-only knowledge MCP server, and five unattended nightly loops that drive Claude Code, Codex and Grok through 1,300-line bash wrappers. The chat UI is the thin part. What is missing is the layer the industry now calls the harness: one place that assembles context, owns the tool registry and its permission tiers, enforces domain invariants deterministically before any side effect, records a replayable session, and can be pointed at any model. The recommendation is not to adopt Pi or the Claude Agent SDK wholesale, and not to rewrite from scratch. It is to extract the loop that already exists in `web/lib/assistant/loop.ts` into a standalone, provider-neutral `harness/` package modeled on Pi's minimal core (loop, typed tools, events, JSONL session, extension hooks) while keeping Radon's FastAPI routes, capability pins, gates and MCP knowledge base as the tool surface. Order placement stays exactly where it is today: a proposal the harness cannot execute, confirmed by a human through the existing `OrderRiskGate` and server-side `order_limits` + `trading_halt` chokepoints. Evals come from the trade journal and the reliability audit, not from synthetic prompts.

---

## 1. What an agent harness is (2025 to 2026 practice)

### 1.1 Definition

The term has converged. Addy Osmani's summary, quoting Viv Trivedy: "Agent = Model + Harness. If you're not the model, you're the harness," with the gloss that "a decent model with a great harness beats a great model with a bad harness" ([Osmani, Agent Harness Engineering](https://addyosmani.com/blog/agent-harness-engineering/)). OpenAI's Codex team, describing the open-sourced Codex runtime, defines the harness as "the execution system that sits between a model and a task, which gathers context, invokes tools, enforces sandbox and approval boundaries, streams execution progress, and carries work across multi-turn sessions" ([OpenAI, Unlocking the Codex harness](https://openai.com/index/unlocking-the-codex-harness/); summarized via [floatboat](https://floatboat.ai/blog/codex-harness-open-source) since the original returned 403 to this fetch). Anthropic's Agent SDK docs describe the same thing from the vendor side: "The Agent SDK gives you the same tools, agent loop, and context management that power Claude Code, programmable in Python and TypeScript" ([Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)).

"Harness engineering" as a discipline traces to Mitchell Hashimoto (Feb 2026): "Anytime you find an agent makes a mistake, you take the time to engineer a solution such that the agent never makes that mistake again" ([Hashimoto](https://mitchellh.com/writing/my-ai-adoption-journey)), then OpenAI's post on building ~1M lines with Codex where "Agents are most effective in environments with strict boundaries and predictable structure" and review comments become lint rules ([OpenAI, Harness engineering, via ai-wiki summary](https://businessdatasolutions.github.io/ai-wiki/sources/2026-02-11-lopopolo-codex-harness-engineering)).

So: a **chat UI** renders turns; a **prompt** is one input to one call; a **model** produces tokens. A **harness** is the runtime that turns a model into an agent: it decides what the model sees, what it may do, what actually happens when it asks, and what is written down afterwards.

### 1.2 Component checklist, with primary sources

| Component | What it does | Primary reference |
|---|---|---|
| Context assembly | System prompt + project instruction files + just-in-time retrieval, curated against a finite "attention budget" | Anthropic: context engineering is "curating and maintaining the optimal set of tokens" ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)); Pi loads `AGENTS.md`/`CLAUDE.md` walking up the tree, plus `SYSTEM.md` and `APPEND_SYSTEM.md` ([pi README](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)) |
| Tool registry + call loop | Typed schemas the model can call; loop runs until a response has no tool calls | Agent SDK: "Claude continues calling tools and processing results until it produces a response with no tool calls" ([Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)); Pi `AgentTool` with TypeBox `parameters` and `execute(toolCallId, params, signal, onUpdate)` ([pi-agent README](https://github.com/badlogic/pi-mono/blob/main/packages/agent/README.md)) |
| Permission / approval | Ordered evaluation: hooks, deny rules, ask rules, mode, allow rules, callback | Agent SDK permissions: "hooks run before every other step, and a hook deny applies even in `bypassPermissions` mode" ([Permissions](https://code.claude.com/docs/en/agent-sdk/permissions)); OpenAI Agents SDK `needs_approval` pauses the run and serializes `RunState` ([Human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)) |
| Sandboxing | OS-level isolation of the tool executor | Codex: read-only / workspace-write / danger-full-access, Seatbelt on macOS, Landlock+seccomp on Linux ([Codex security](https://learn.chatgpt.com/docs/security)); OpenHands runs the agent in Docker with a mounted projects dir ([OpenHands](https://github.com/OpenHands/OpenHands)); Pi ships none and says "Run in a container, or build your own confirmation flow" ([pi README](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)) |
| Memory (session / project / long-term) | Transcript on disk; project files; editable long-term blocks | Agent SDK sessions under `~/.claude/projects/<cwd>/*.jsonl` with resume/fork ([Sessions](https://code.claude.com/docs/en/agent-sdk/sessions)); Letta memory blocks + archival, "MemFS" git-tracked ([Letta](https://docs.letta.com/concepts/letta)); Anthropic structured note-taking outside the window ([Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)) |
| Compaction | Summarize older history when the window fills | Pi: triggers when `contextTokens > contextWindow - reserveTokens` (16,384 default), keeps last ~20k tokens, tracks read/modified files across compactions, extensions can supply the summary via `session_before_compact` ([pi compaction](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/compaction.md)); Agent SDK emits `compact_boundary` and offers a `PreCompact` hook ([Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)) |
| Sub-agents / orchestration | Fresh-context workers returning summaries | Anthropic research system: "Each subagent needs an objective, an output format, guidance on the tools and sources to use, and clear task boundaries" ([Multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)); LangGraph positions itself as "the orchestration runtime: durable execution, streaming, human-in-the-loop, and persistence" ([LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)); Pi deliberately leaves sub-agents to extensions ("a black box within a black box", [Zechner](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)) |
| Evals | Task suites scored on end state | Anthropic: "Evaluate whether it achieved the correct final state" and a single LLM judge with a 0 to 1 rubric "was the most consistent and aligned with human judgements" ([Multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)); tool-design eval loop in [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) |
| Observability / tracing | Event stream per turn and per tool | OpenAI Agents SDK: "Built-in tracing for visualizing, debugging, and monitoring workflows" ([Agents SDK](https://openai.github.io/openai-agents-python/)); Pi emits `agent_start … tool_execution_start/update/end … agent_end` and has a `pi-telemetry` package ([pi-agent README](https://github.com/badlogic/pi-mono/blob/main/packages/agent/README.md)) |
| Cost / quotas | Turn and dollar caps | Agent SDK `max_turns`, `max_budget_usd`, `ResultMessage.total_cost_usd` ([Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)) |
| Resumability | Resume by id, fork, cross-host stores | Agent SDK `resume`, `fork_session`, `SessionStore` adapter ([Sessions](https://code.claude.com/docs/en/agent-sdk/sessions)); Pi sessions are a JSONL tree with `id`/`parentId`, `/tree`, `/fork` ([pi README](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)); OpenAI Agents `RunState.to_json()` "designed to be durable" ([Human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)) |

Two framework observations relevant to Radon. smolagents' pitch is that "the logic for agents fits in ~thousand lines of code" and it is model-agnostic through LiteLLM ([smolagents](https://huggingface.co/docs/smolagents/index)); that is roughly the size of Radon's existing `loop.ts` + `tools.ts` + `dispatch.ts` (1,771 lines). Anthropic's original taxonomy separates "workflows" (LLMs and tools orchestrated through predefined code paths) from "agents" (LLMs dynamically direct their own processes) and recommends "poka-yoke" tool design ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Radon's 7-milestone evaluate is a workflow; the chat assistant is an agent; a Radon harness has to host both.

---

## 2. Pi, studied

### 2.1 Which Pi

Confirmed: `pi` is the coding agent by Mario Zechner (libGDX author), repo [badlogic/pi-mono](https://github.com/badlogic/pi-mono), site [pi.dev](https://pi.dev), npm `@mariozechner/pi-coding-agent`. The repo README now lists the maintainer as the `earendil-works` GitHub org (Armin Ronacher's company Earendil, per [implicator.ai](https://www.implicator.ai/pi-is-not-a-claude-code-rival-it-is-a-harness-rebellion/)) with Zechner still publishing sessions; the runtime packages are published as `@earendil-works/pi-ai` and friends. MIT license, ~45k stars as of May 2026 ([dev.to](https://dev.to/rigor120000/underrated-ai-coding-agent-harnesses-in-2026-pi-mini-swe-agent-crush-plandex-amp-and-more-5c0a)). Not to be confused with Radon's own `/api/pi` route, which is a legacy command dispatcher (`web/app/api/pi/route.ts`, allowlisted `scanner.py`/`evaluate.py`/`ib_sync.py` via FastAPI `/pi/exec`, see `scripts/api/CLAUDE.md`).

### 2.2 Package split (the harness/agent separation)

| Package | Role (verbatim from README) |
|---|---|
| `pi-ai` | "Unified multi-provider LLM API (OpenAI, Anthropic, Google, …)" |
| `pi-agent-core` | "Agent runtime with tool calling and state management" |
| `pi-coding-agent` | "Interactive coding agent CLI" |
| `pi-tui` | "Terminal UI library with differential rendering" |
| `pi-telemetry` | "Vendor-neutral telemetry contracts, reference adapter, conformance tests" |
| `chord` | "Standalone application-composition runtime for services, replicated state, RPC, and plugins" |

The important design fact is that the harness is the two bottom layers (`pi-ai` + `pi-agent-core`) and the coding agent is a consumer. Nothing in the agent runtime knows about files or bash; those are tools registered by the coding agent.

### 2.3 Core loop

From the `pi-agent-core` README: the message pipeline is `AgentMessage[] → transformContext() → AgentMessage[] → convertToLlm() → Message[] → LLM`. `transformContext` is an optional hook to "prune old messages, inject external context"; `convertToLlm` is required and "LLMs only understand `user`, `assistant`, and `toolResult`. The `convertToLlm` function bridges this gap." The `AgentState` is small: `systemPrompt, model, thinkingLevel, tools, messages, isStreaming, streamingMessage, pendingToolCalls, errorMessage`. The loop is event-driven (`agent_start`, `turn_start`, `message_start/update/end`, `tool_execution_start/update/end`, `turn_end`, `agent_end`), and the provider is injected as a `streamFn` "allowing implementations via pi-ai providers or proxy wrappers." Zechner's own description: the loop "handles the full orchestration: processing user messages, executing tool calls, feeding results back to the LLM, and repeating until the model produces a response without tool calls," and he "never found a use case" for a max-step cap ([Zechner](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)).

### 2.4 Tools

```ts
const readFileTool: AgentTool = {
  name: "read_file",
  description: "Read a file's contents",
  parameters: Type.Object({ path: Type.String({ description: "File path" }) }),
  execute: async (toolCallId, params, signal, onUpdate) => { /* ... */ },
};
```
Parameters are TypeBox schemas (validated before `execute`), `signal` is an `AbortSignal`, `onUpdate` streams progress, and tools "should throw errors on failure rather than returning error content." The coding agent ships only `read`, `write`, `edit`, `bash` (plus `grep`, `find`, `ls` discoverable), on the argument that "these four tools are all you need for an effective coding agent" and the whole prompt plus tool definitions stays "below 1000 tokens" ([Zechner](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)).

### 2.5 Extensions, skills, and what is deliberately absent

Extensions are TypeScript modules: `pi.registerTool`, `pi.registerCommand`, `pi.on(event, handler)`, `pi.sendMessage`, `pi.appendEntry` for persisted state. The `tool_call` event fires before execution and **can block**: a handler returns `{ block: true, reason, terminate? }`, and the documented example is a confirmation gate on `rm -rf` via `ctx.ui.confirm` ([extensions.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md)). Other hooks: `before_agent_start` (modify system prompt, inject messages), `context` (non-destructive message rewrite before each LLM call), `session_start` (rebuild state from custom entries), `session_before_compact`. Skills follow the [Agent Skills](https://agentskills.io) markdown standard and load from `.pi/skills/` or `.agents/skills/`, so Radon's `.claude/skills/*/SKILL.md` are already in a compatible shape.

Excluded from core on purpose, all buildable as extensions: MCP ("7-9% of your context window gone before you even start working"), sub-agents, permission popups, plan mode, to-dos, background bash. Zechner: "if I don't need it, it won't be built."

### 2.6 Sessions, compaction, modes

Sessions are JSONL trees (`id`/`parentId`), branchable in place, auto-saved, exportable to HTML. Compaction is "lossy. The full history remains in the JSONL file; use `/tree` to revisit." Four modes: interactive TUI, `-p` print, `--mode json`, `--mode rpc` (JSONL over stdin/stdout with `prompt`, `steer`, `follow_up`, `abort`, `get_state`, `set_model`, and `extension_ui_request/response` so a host can answer confirmations) plus an SDK (`createAgentSession()`). RPC mode is what makes Pi embeddable behind a web UI without the TUI ([rpc.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md)).

### 2.7 Why it is a good study for Radon

1. It proves the loop can be small. Radon's `loop.ts` is already close to Pi's loop in shape; the difference is Pi's loop is a library and Radon's is welded to a Next.js route.
2. The `tool_call` block hook is exactly the seam Radon needs for gates: deterministic code that sees the typed input before execution and can refuse with a reason the model reads.
3. Provider-agnostic by construction (`streamFn`), which matches the multi-provider ladder Radon already runs in the nightly loops.
4. JSONL session trees give replay and forking for free, which section 3 shows is the finance-specific requirement.
5. Its omissions are Radon's omissions: no sandbox (Radon's tools are HTTP calls, not shell), no MCP in core (Radon has one MCP server and can adapt it as a tool set), and no permission popups (Radon has a confirm UI and server chokepoints already).

What not to copy: the four file tools and bash. A Radon harness should have zero shell access in the trading agent; its tools are the FastAPI/Next catalog.

---

## 3. Best practices for domain-specific harnesses

### 3.1 Invariants as code, not prose

The finance literature from 2026 is unanimous. The Lean-Agent Protocol paper argues that guardrail products "rely on probabilistic classifiers and syntactic validators that are fundamentally inadequate for enforcing complex multi-variable regulatory constraints" and proposes that "execution is permitted if and only if" a deterministic kernel proves the action satisfies pre-compiled rules, explicitly modeled on SEC Rule 15c3-5 pre-trade controls ([Type-Checked Compliance, arXiv 2604.01483](https://arxiv.org/abs/2604.01483)). FinHarness wraps "a finance agent end-to-end" with a query monitor, a tool monitor evaluating "each prospective tool call before execution," and a cascade that escalates rather than auto-approves; on FinVault it cut attack success from 38.3% to 15.0% while keeping benign approvals near baseline ([FinHarness, arXiv 2605.27333](https://arxiv.org/abs/2605.27333)). A third paper, "LLM-as-a-Judge Is Not an Oracle," makes the general case that self-improving agents need deterministic guardrails rather than model judges ([arXiv 2609.02246](https://arxiv.org/abs/2609.02246)).

**Radon already does this, partially, and it is the right pattern.** Verified in code:
- `scripts/workflow/gates.py` encodes Gate 1 as `convexity_gate(max_gain, max_loss)` with `CONVEXITY_MIN_RATIO = 2.0` and "an undefined / unbounded loss always fails," and Gate 3 as `kelly_gate` delegating to the single `kelly.kelly` implementation. `GateResult.gate` is the canonical name "so the executor can name the failing gate exactly as the Four-Gates rule demands."
- `scripts/evaluate.py` carries `failing_gate: TICKER_VALIDATION | EDGE | CONVEXITY | RISK` and `determine_edge` uses numeric thresholds (aggregate flow strength > 50, direction not NEUTRAL).
- `scripts/order_limits.py` (REL-005): server-side fat-finger caps on qty, notional, combo worst-case loss, orders per minute, orders per workflow run, "the client-side risk UI remains a display, never the enforcement."
- `scripts/trading_halt.py` (REL-004): kill switch where "unreadable / malformed → HALTED," checked by `_refuse_if_trading_halted()` at four FastAPI order routes (`server.py:2924, 3106, 3206, 3324`).
- `web/lib/nakedShortGuard.ts` keeps Gate 4 as `_checkNakedShortRiskImpl` / `_auditOpenOrdersImpl` for re-enable.

What is **not** true today: the chat assistant's system prompt (`web/app/api/assistant/route.ts:SYSTEM_PROMPT`) states the gates as prose ("flags convexity: gain >= 2x loss") and the only structural gate in the loop is "destructive tool halts." Gates 1 to 3 are not evaluated by the harness on a proposal before it reaches the confirm UI; `rank_spreads` computes convexity as a tool output the model may or may not respect.

### 3.2 Typed tools over free-form shell

Anthropic: "More tools don't always lead to better outcomes"; consolidate into "a few thoughtful tools targeting specific high-impact workflows," namespace them, return "only high signal information," and treat descriptions "like you would describe your tool to a new hire" ([Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)). Radon's `tools.ts` follows this: 17 named tools with JSON schemas, plus `list_apis`/`call_api` derived from route pins rather than a third handwritten catalog (`web/CLAUDE.md` "Assistant catalog pin"). The Agent SDK adds a useful bit: custom tools default to sequential execution and only run in parallel when annotated `readOnlyHint` ([Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)). Radon's six capability tiers (`read`, `read.spawn`, `mutate.workspace`, `mutate.trading`, `admin`, `internal`, in `web/lib/assistant/capabilities.ts`) are a richer version of that annotation and should become the harness's permission vocabulary.

### 3.3 The harness checks, the model proposes

Anthropic's ground-truth principle: agents need "environmental feedback at each step" and "human checkpoints" with "stopping conditions" ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). OpenAI's Agents SDK formalizes this as input, output and per-tool guardrails with tripwire exceptions, and `run_in_parallel=False` when "the agent never executes, preventing token consumption and tool execution" ([Guardrails](https://openai.github.io/openai-agents-python/guardrails/)). In practice for Radon: every `place_order` proposal must pass `convexity_gate`, `kelly_gate` (with the 2.5% cap), `order_limits`, and `trading_halt` inside the harness, deterministically, before the confirm card renders. The model's job is to fill the typed proposal; the harness's job is to say which gate failed.

### 3.4 Read-only vs write, and irreversible actions

Codex's default posture (read-only or workspace-write sandbox, approval "untrusted"/"on-request"/"never") and the Agent SDK's evaluation order both put deny-before-allow. The OpenAI Agents SDK's `needs_approval` "fail[s] closed when the SDK cannot safely inspect the arguments" ([Human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)). Radon's loop matches: `validateAssistantOrderInput` returns `null` on any malformed field and the loop answers "cannot be confirmed"; `hasExplicitOrderIntent` requires current-turn language; sibling destructive calls are deferred until reads complete. Keep all of it.

### 3.5 Audit logs and replayable sessions

The "Replayable Financial Agents" paper introduces a determinism-faithfulness harness and finds "decision determinism and task accuracy are not detectably correlated (r = -0.11)," so both must be measured, and notes regulators expect an agent to "reproduce a flagged transaction decision with identical inputs" ([arXiv 2601.15322](https://www.alphaxiv.org/abs/2601.15322)). Radon records one `assistant_turns` row per turn (migration `0030`, provenance columns in `0065`: provider, model, error class, image count) but does not persist the message array, tool inputs, or tool outputs; the client owns the transcript (up to 40 messages per request). That is an audit gap: a proposal cannot be replayed from what Turso holds.

### 3.6 Model swappability

Every surveyed framework treats the provider as a plug: Pi `streamFn`, OpenAI Agents via LiteLLM, smolagents via LiteLLM/Transformers/Ollama, Letta "all major frontier model providers." Radon's `web/lib/llm/provider.ts` already normalizes xAI, Anthropic, OpenAI-compatible and Gemini behind one `chat()`, and the nightly loops run a `codex → grok → nvidia → cerebras` ladder with `claude` reserved for security (`docs/operations.md`). One caveat from the Agent SDK docs: "Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK" ([SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)). Radon's loops deliberately ride the claude.ai subscription through `claude -p` and unset every API-key variable (`docs/operations.md` line 254); an Agent-SDK-based harness would move that spend to API keys.

---

## 4. Recommendation for Radon

### 4.1 Build vs adopt

| Option | Fit | Verdict |
|---|---|---|
| Claude Agent SDK | Best-in-class permissions order, hooks, sessions, cost caps, subagents. But tools are Claude Code's file/bash set plus MCP; the trading agent should have none of those. Locks the interactive harness to Claude and API-key billing. | Adopt for the **nightly engineering loops** (replace bash wrappers' `claude -p --dangerously-skip-permissions` with SDK `dontAsk` + `disallowedTools`, `PreToolUse` hooks, `max_budget_usd`). Not for the trading harness. |
| Pi (as dependency) | Loop, TypeBox tools, JSONL sessions, extension `tool_call` block hook, RPC mode. TypeScript, same language as `web/`. Core is small enough to read. | Adopt the **design**, and optionally `@earendil-works/pi-agent-core` + `pi-ai` as packages, behind Radon's own interfaces. Do not adopt the coding-agent CLI. |
| OpenAI Agents SDK | Guardrails and `RunState` approvals are the cleanest HITL model surveyed. Python. | Borrow the guardrail/tripwire and durable-interruption ideas; not worth a second runtime. |
| LangGraph / Letta / OpenHands / smolagents | Orchestration, memory, sandbox, code-agents respectively. | Not needed; Radon's tools are HTTP and its orchestration is milestone-shaped. |
| Build from scratch | Radon already did, in `loop.ts`. | The work is extraction and hardening, not a rewrite. |

**Recommendation: extract and extend.** Take the existing loop out of the Next.js route into a standalone TypeScript package with Pi's shape, keep every Radon-specific guard already in it, and add the four things it lacks: gates-as-code on proposals, persisted replayable sessions, a hook layer, and compaction.

### 4.2 Target architecture (reusing what exists)

```
                  +---------------------------------------------+
  ChatPanel  ---> |  harness/core                                |
  (web)           |  loop . events . session JSONL . compaction  |
  nightly job --> |  provider = web/lib/llm/provider.ts chat()   |
  CLI (-p/rpc)    +---------+--------------+--------------+------+
                            |              |              |
                     tool registry    hook pipeline   session store
                     (typed, tiered)  pre/post tool   (Turso + disk)
                            |              |
          +-----------------+--------------+----------------+
          |                 |                               |
    FastAPI catalog    radon-kb MCP                   gates-as-code
    (assistant_catalog  (search/prior evals,           convexity . kelly .
     + route pins)       SELECT-only)                  order_limits . halt
          |                                                  |
          +------ place_order = PROPOSAL only ---------------+
                            |
                  OrderRiskGate confirm (human) -> /api/orders/place -> FastAPI -> IB
```

Verified reuse points:
- **Tool surface**: `ASSISTANT_TOOLS` in `web/lib/assistant/tools.ts` and the pin-derived catalog (`web/lib/assistant/catalog.ts`, `scripts/api/assistant_catalog.py`, tests `assistant-catalog-pin`, `-freshness`, `-parity`). Capability tiers become the harness permission table; `REFUSED = {admin, internal, mutate.trading}` stays.
- **Knowledge**: `scripts/knowledge/mcp_server.py` is already "LLM-free retrieval primitives: the agent orchestrates, these tools just retrieve," SELECT-only by pin test. The loop's `isolateKnowledgeResult` (second-model fact extraction, untrusted-content fences) is a prompt-injection boundary worth keeping as a post-tool hook.
- **Gates**: `scripts/workflow/gates.py` exists in Python; the harness needs the same two functions in TypeScript (or a FastAPI `POST /gates/evaluate` that the harness calls, which keeps one implementation). `order_limits` and `trading_halt` are already server-side and remain the final backstop.
- **Skills**: `.claude/skills/*/SKILL.md` and `.claude/portable-prompts/*.md` (rendered by `scripts/render_loop_prompt.py`, parity-tested) are the harness's skill files; Pi reads the same Agent Skills format.
- **Loops**: the five wrappers' phase contract (audit/remediate/deliver, verdict lines, dead-man issue comments, provider ladder, quota regexes) is harness policy written in bash. It should become a headless driver of the same core.
- **Telemetry**: `assistant_turns` extends to full session entries; `web/lib/agent/turnSteps.ts` already renders tool events as a trace.

### 4.3 Phased plan

**Phase 0: extract from the current assistant (1 to 2 weeks).**
- Move `loop.ts`, `tools.ts`, `dispatch.ts`, `catalog*.ts`, `capabilities.ts`, `backend.ts` into `harness/` with no behavior change; `web/app/api/assistant/route.ts` becomes a thin adapter. The 25 `web/tests/assistant-*.test.ts` files are the regression net; they must pass unchanged.
- Replace the inline `SYSTEM_PROMPT` string with a context assembler: base persona + `docs/evaluation.md` gate table + journal conventions, each a file, so the prompt is versioned and the compactor can re-inject it (Agent SDK: "Persistent rules belong in CLAUDE.md … because CLAUDE.md content is re-injected on every request").
- Emit Pi-style events (`turn_start`, `tool_execution_start/end`, `turn_end`) from the loop and stream them; `turnSteps.ts` consumes them.

**Phase 1: harness core (2 to 3 weeks).**
- Session store: append every entry (user, assistant blocks, tool_use, tool_result, proposal, gate verdicts) as JSONL with `id`/`parentId` to disk and mirror to a new Turso `assistant_sessions` table (migration + production row check per CLAUDE.md "Data Persistence"). Resume and fork by id.
- Hook pipeline: `before_agent_start` (context injection), `tool_call` (can block with reason), `tool_result` (knowledge isolation, truncation), `session_before_compact`. Port the existing guards (repeated-call nudge, spawn budget, explicit-intent check) into hooks so they are visible and testable individually.
- Compaction: Pi's reserve/keep-recent algorithm; preserve tool names, tickers touched, gate verdicts and proposals in the summary.
- Budgets: per-turn round cap (today `MAX_ROUNDS = 8`), per-session token and dollar cap from `usage`, surfaced in the result like the Agent SDK's `error_max_budget_usd`.

**Phase 2: gates-as-code and tool typing (2 weeks).**
- `place_order` gains a mandatory `gate_evaluation` step in the `tool_call` hook: compute max gain / max loss from the priced legs (`rank_spreads`/`spreads.ts` already prices verticals), run convexity, Kelly with bankroll from `get_portfolio`, `order_limits`, `trading_halt`. A failing gate returns `{ block: true, reason: "GATE 1 convexity: 1.4x < 2x" }` and the proposal never renders. Test at the wire per CLAUDE.md: assert the blocked call produced no `/api/orders/place` request.
- Tool schemas move to one typed registry (TypeBox or zod) with `capability`, `readOnly`, `timeoutMs`, `resultCap` metadata; `call_api` keeps the catalog but the harness enforces tier before dispatch, not the tool body.
- Edge gate (Gate 2) stays model-assisted but structured: require a `signal` object (source, ticker, direction, strength, timestamp, has_moved_price) on every proposal; the hook rejects proposals without one.

**Phase 3: evals from the trade journal (2 weeks, ongoing).** See section 5.

**Phase 4: multi-model (1 to 2 weeks).**
- Provider ladder from the wrappers becomes harness config (`provider[:model]` rungs, quota and session-limit regexes already captured in `reliability_weekend.sh:970-1005`).
- Nightly loops: replace `claude -p "/skill phase" --dangerously-skip-permissions` with the harness in headless mode driving either the Claude Agent SDK (Claude rungs, `permissionMode: "dontAsk"`, `disallowedTools` for IB/gateway paths, `PreToolUse` hooks enforcing the "never touch IB Gateway / never place an order" rails from `SKILL.md`) or the Codex/Grok CLIs through the existing portable prompts. The wrappers shrink to process supervision.
- Local models via OpenAI-compatible base (`provider.ts` already routes Ollama).

### 4.4 Suggested repo layout

```
harness/
  package.json                 # @radon/harness, TS, no Next.js imports
  src/
    core/loop.ts               # from web/lib/assistant/loop.ts
    core/events.ts             # Pi-style event union
    core/session.ts            # JSONL tree + Turso mirror, resume/fork
    core/compaction.ts
    core/budget.ts
    context/assemble.ts        # persona + docs + skills + retrieved notes
    context/prompts/*.md       # versioned prompt fragments
    tools/registry.ts          # typed tools + capability + readOnly
    tools/radon/*.ts           # get_flow, rank_spreads, run_evaluate, ...
    tools/catalog/*.ts         # list_apis / call_api (pin-derived)
    tools/knowledge/*.ts       # radon-kb over MCP or HTTP
    hooks/pipeline.ts
    hooks/gates.ts             # convexity, kelly, limits, halt (block hook)
    hooks/intent.ts            # explicit-order-intent, sibling-read deferral
    hooks/untrusted.ts         # knowledge isolation
    providers/index.ts         # wraps web/lib/llm/provider.ts chat()
    modes/rpc.ts               # JSONL stdin/stdout for wrappers
    modes/print.ts
  tests/                       # moved assistant-*.test.ts + gate wire tests
  evals/
    suites/*.jsonl             # section 5
    run.ts
scripts/harness/               # Python side: FastAPI /gates/evaluate, loop drivers
```

### 4.5 Specific risks

1. **Order placement must never be a free-form tool call.** Today it is a typed proposal and the harness returns before executing; the confirm click hits `/api/orders/place`, which enforces idempotency, demo blockade, `order_limits`, and `trading_halt`. Keep that shape: no harness mode, hook, or "auto-approve" flag may resolve a `mutate.trading` tool. Encode it as a pin test that greps the harness for any dispatch of a `mutate.trading` capability, the same way `mcp_server.py` is pinned SELECT-only.
2. **`RELIABILITY_AUDIT.md` is the threat model.** Its executive summary: "at least five code paths reach IB `placeOrder` with nothing but `quantity > 0` between them and the margin engine" (since remediated by REL-004/005). A harness adds a sixth path only if it can reach FastAPI order routes; the capability catalog refuses them, and that refusal must be tested at the wire.
3. **Secrets.** The harness inherits the wrappers' rule: load only what a phase needs, never import `.env` wholesale, and never let a model-readable tool result contain a key. The `PreToolUse` git-commit secret grep in `.claude/settings.json` is the pattern; add a `tool_result` hook that redacts known key shapes.
4. **Cost.** Moving interactive chat off subscription to API keys is already the case (`web/.env` keys). Moving the nightly loops onto the Agent SDK would move them to API billing; keep the codex/grok ladder on those loops unless the security loop's Claude exclusivity justifies SDK spend. Add `max_budget_usd` per phase.
5. **Prompt injection through knowledge and newsfeed.** The existing untrusted-content fences and second-model extraction are good; a harness must not lose them in the refactor. Test `assistant-untrusted-knowledge.test.ts` pins it.
6. **Cwd-coupled context.** Sub-directory `CLAUDE.md` files load on cwd for Claude Code; the harness's context assembler must load them explicitly by path, otherwise the trading agent silently loses `scripts/api/CLAUDE.md`'s order-lane rules.
7. **radon-kb MCP fragility.** In this session the server failed to spawn (`ENOENT .venv/bin/python`). The harness should call the knowledge functions in-process or over FastAPI rather than depend on a stdio child that a clone without `.venv` cannot start.

---

## 5. Evaluation: how to know the harness beats the chat UI

Build suites from Radon's own history, score end state, and keep a deterministic replay lane alongside an LLM-judge lane.

| Suite | Source (verified) | Task shape | Score |
|---|---|---|---|
| Evaluate replays | `scripts/evaluate.py` results persisted to Turso and the radon-kb `evals` source (`reports/*.html`, empty in this credential-free clone); `docs/status.md` now says "Trade evaluations live in Turso and the radon-kb corpus" | "Evaluate TICKER as of DATE" with tool results frozen from the original run | Same `failing_gate` and same PASS/FAIL as the recorded run; structure R:R within tolerance |
| NO_TRADE decisions | Historical NO_TRADE outcomes in the eval corpus (former `docs/status.md` log) | Same as above where the truth is "stop, name the gate" | Harness must stop at the same gate; any proposal is a fail |
| Proposal gating | Journal rows (`journal.payload`, lot-matched via `get_realized_pnl`) for executed trades | "Given this flow and chain, propose a structure" | Deterministic: convexity and Kelly gates pass; wire: exactly one proposal, zero `/api/orders/place` requests |
| Knowledge retrieval | `scripts/knowledge/golden_set.json` (draft, operator to curate), `eval_golden.py` hit@5 >= 0.8 gate | Golden questions | hit@5, already implemented |
| Incident reasoning | `RELIABILITY_AUDIT.md` (2,458 lines, 253 distinct REL ids) and `tasks/lessons.md` via the docs connector | "What happens when Turso blips during exit-order placement?" | LLM judge with rubric on citing the right finding id and mechanism |
| Loop phases | Nightly issue comments and PRs (`reliability/<date>` etc.) | Re-run an audit phase against a frozen SHA range | Same finding count and ids as the recorded run |
| Determinism | Per the replayable-agents paper, run each replay N times | Any suite | Decision determinism rate, reported separately from accuracy |

Comparison protocol: run the current `/api/assistant` route and the harness against the same frozen tool fixtures (the loop already supports `ASSISTANT_MOCK`), same model, and compare per-suite pass rate, tokens per turn (`usage` is already accumulated), rounds per turn, and the number of turns that ended `cap_fallback`. The harness is better when it matches or beats the chat UI on every suite, blocks 100% of gate-violating proposals, and every session can be replayed from Turso alone.

---

## Sources

- https://addyosmani.com/blog/agent-harness-engineering/
- https://openai.com/index/unlocking-the-codex-harness/ (403 to this fetch; summarized via https://floatboat.ai/blog/codex-harness-open-source)
- https://openai.com/index/harness-engineering/ (403 to this fetch; summarized via https://businessdatasolutions.github.io/ai-wiki/sources/2026-02-11-lopopolo-codex-harness-engineering)
- https://mitchellh.com/writing/my-ai-adoption-journey
- https://code.claude.com/docs/en/agent-sdk/overview
- https://code.claude.com/docs/en/agent-sdk/agent-loop
- https://code.claude.com/docs/en/agent-sdk/permissions
- https://code.claude.com/docs/en/agent-sdk/sessions
- https://code.claude.com/docs/en/hooks
- https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code
- https://www.anthropic.com/engineering/building-effective-agents
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://www.anthropic.com/engineering/writing-tools-for-agents
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://openai.github.io/openai-agents-python/
- https://openai.github.io/openai-agents-python/guardrails/
- https://openai.github.io/openai-agents-python/human_in_the_loop/
- https://github.com/openai/codex
- https://learn.chatgpt.com/docs/security
- https://github.com/badlogic/pi-mono
- https://pi.dev
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md
- https://github.com/badlogic/pi-mono/blob/main/packages/agent/README.md
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/compaction.md
- https://mariozechner.at/posts/2025-11-30-pi-coding-agent/
- https://www.implicator.ai/pi-is-not-a-claude-code-rival-it-is-a-harness-rebellion/
- https://dev.to/rigor120000/underrated-ai-coding-agent-harnesses-in-2026-pi-mini-swe-agent-crush-plandex-amp-and-more-5c0a
- https://github.com/OpenHands/OpenHands
- https://huggingface.co/docs/smolagents/index
- https://docs.langchain.com/oss/python/langgraph/overview
- https://docs.letta.com/concepts/letta
- https://arxiv.org/abs/2604.01483 (Type-Checked Compliance / Lean-Agent Protocol)
- https://arxiv.org/abs/2605.27333 (FinHarness)
- https://arxiv.org/abs/2609.02246 (LLM-as-a-Judge Is Not an Oracle)
- https://www.alphaxiv.org/abs/2601.15322 (Replayable Financial Agents)
- https://agentskills.io

Radon files read (all under the clone root): `CLAUDE.md`, `web/CLAUDE.md`, `scripts/api/CLAUDE.md`, `docs/evaluation.md`, `docs/operations.md` (lines 254 to 350), `docs/status.md`, `web/lib/assistant/{loop,tools,dispatch,catalog,capabilities,backend,telemetry}.ts`, `web/app/api/assistant/route.ts`, `web/lib/llm/{provider,catalog,frontier}.ts`, `web/lib/agent/turnSteps.ts`, `web/lib/order/risk/OrderRiskGate.tsx`, `web/lib/nakedShortGuard.ts`, `web/app/api/orders/place/route.ts`, `scripts/workflow/{gates,__init__,nodes}.py`, `scripts/{order_limits,trading_halt,kelly,evaluate}.py`, `scripts/api/{server,assistant_catalog}.py`, `scripts/knowledge/{mcp_server,eval_golden,golden_set.json}`, `scripts/knowledge/sources/{evals,incidents}.py`, `.claude/settings.json`, `.claude/skills/reliability-weekend/SKILL.md`, `.claude/skills/long-horizon-jobs/SKILL.md`, `scripts/reliability_weekend.sh`, `RELIABILITY_AUDIT.md` (header and sections 1 to 2.4), `scripts/db/migrations/{0001_init,0030_assistant_turns,0065_assistant_turns_provenance}.sql`, `.mcp.json`.

## Open questions for the operator

1. Should the Python gates (`scripts/workflow/gates.py`) be the single implementation exposed over FastAPI, or is a TypeScript twin acceptable with a parity test? One implementation is cleaner; a twin avoids a network hop on every proposal.
2. Where do historical eval reports live now that `docs/status.md` is not a log and `reports/` is empty in the security clone? The eval suite in section 5 depends on being able to enumerate them from Turso.
3. Is the nightly-loop spend model (claude.ai subscription for security, codex/grok/nvidia/cerebras for the rest) fixed? It decides whether the Claude Agent SDK is viable for the loops at all.
4. Should Gate 2 (edge) remain model-assisted with a required structured `signal` object, or do you want a numeric edge threshold (like `determine_edge`'s strength > 50) enforced by the hook as well?
5. Do you want the harness to own the `/api/pi` legacy command path (`scanner`, `evaluate`, `sync`) or retire it once the harness's typed tools cover the same commands?
6. Is a Pi dependency (`@earendil-works/pi-agent-core`) acceptable given the ownership change to Earendil, or do you prefer a Radon-owned loop with Pi as reference only?
