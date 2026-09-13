# Training a small language model on Radon's data: would it even be useful?

Date: 2026-09-12
Scope: research report for the Radon operator. Repository read-only at `radon-security/.claude/worktrees/loops-session-limit`. No production database was queried; all corpus figures come from repo docs and schema.

## Executive summary

Partly, and not in the way the statement imagines. "AI constantly studying my portfolio, news and trades" conflates three things that need three different mechanisms. Daily-changing knowledge (positions, flow, headlines) belongs in retrieval, and Radon already has that: a 4,933-document hybrid FTS5 + vector knowledge base at hit@5 0.917, refreshed hourly, wired into the assistant and the `radon-kb` MCP server. Fine-tuning weights on that stream would be slower, staler and more hallucination-prone than what exists (the 2024-2026 literature is unambiguous on this). Skills, format and house rules (the four gates, the milestone report shape, the tag taxonomy, the operator's voice) are what fine-tuning captures well, and Radon has just enough decision traces (roughly 150 substantive eval reports and a few hundred thesis-bearing journal entries) for a LoRA on a 4B-9B open model on a Mac Mini to learn them. But the dollar case is weak: the newsfeed tagger costs about $0.003 per post, so a local tagger saves tens of dollars a year, not thousands. Predictive edge ("will this flow signal work") is a classic-ML and backtest question with a tiny sample, and LLM fine-tuning is the single most overfit-prone tool you could point at it. The recommendation: build the decision-trace dataset and eval harness regardless (Phase 0, useful even if no model is ever trained because it makes "tailored to Radon" measurable for the frontier model too), run one bounded distillation experiment on a Qwen3.5-4B or Gemma 4 E4B for tagging and report drafting (Phase 1), and gate any decision-trace SFT on a time-split held-out eval with a rollback rule (Phase 2). Keep signal models in the existing backtest harness (Phase 3). Do not attempt continual weight updates on news or P&L.

## 1. Honest framing: when fine-tuning a small model beats the alternatives

### What is verified in Radon

- Knowledge base shipped 2026-07-20: `tasks/knowledge-base-plan.md` records 4,933 docs from five connectors (819 journal rows, 3,474 newsfeed posts, 183 eval/report files, 86 scan snapshots, 31 docs), golden-set hit@5 0.917 against a 0.8 gate, hourly ingest on the VPS, local bge-small-en-v1.5 embeddings (journal text never leaves the box), Cerebras `gpt-oss-120b` distillation of summaries.
- Query surfaces: `scripts/knowledge/retrieve.py` (BM25 + cosine + recency, RRF fusion), FastAPI `/knowledge/search`, assistant tools `search_knowledge` and `find_prior_evals`, MCP server `scripts/knowledge/mcp_server.py`.
- The assistant loop (`web/lib/assistant/loop.ts`) is an Anthropic tool loop; `web/lib/llm/provider.ts` already abstracts OpenAI-compatible bases including Ollama, so a local model can be plugged in without new plumbing.
- Classic-ML infrastructure exists: `forecast_snapshots` / `forecast_calibration` (Chronos-2 walk-forward, migrations 0015/0016) and `backtest_runs` (0018) with Sharpe, Sortino, Calmar, hit rate, expectancy per run.

### Comparison table

| Option | Where it wins for Radon | Where it loses | Evidence |
|---|---|---|---|
| (a) RAG over the same data with a frontier model | Anything time-varying: positions, flow, news, prior theses. Already built and measured. | Long prompts; retrieval quality caps answer quality. | Ovadia et al. found RAG consistently beat unsupervised fine-tuning for knowledge injection ([arXiv 2312.05934](https://arxiv.org/abs/2312.05934)); a Jan 2026 multi-hop study across three 7B models found RAG delivered "substantial and consistent improvements, particularly when answering questions that rely on temporally novel information" while unsupervised fine-tuning yielded minimal gains ([arXiv 2601.07054](https://arxiv.org/abs/2601.07054)). |
| (b) Long-context prompting | Zero engineering; 1M-token windows on current Claude models make "the whole journal in the prompt" feasible for ad hoc analysis. | Cost per call scales with context; no learning across sessions; not a substitute for retrieval at 5k docs. | Claude Opus 5 and Sonnet 5 offer 1M context ([Anthropic pricing table, claude-api skill cache 2026-06-24](https://docs.claude.com/en/docs/about-claude/pricing)). |
| (c) Fine-tuned frontier via API | In principle the best of both worlds. | Largely unavailable in 2026 for a solo operator. OpenAI: "OpenAI is winding down the fine-tuning platform. The platform is no longer accessible to new users" ([OpenAI SFT guide](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)). Anthropic: the only supported path remains Claude 3 Haiku SFT on Amazon Bedrock us-west-2, 32K context ([Anthropic blog](https://claude.com/blog/fine-tune-claude-3-haiku)), a 2024-generation model. FineTuneBench measured commercial fine-tuning APIs at 37% average generalization accuracy on new facts and 19% on updated facts, with Gemini's APIs "unable to learn new knowledge" ([arXiv 2411.05059](https://arxiv.org/abs/2411.05059)). | Dead end for knowledge; marginal for style. |
| (d) Classic ML on tabular / time series | Predictive questions with a measurable label: flow surprise, vol cone width, regime bands. Backtestable with purged CV and deflated Sharpe. | Needs feature engineering; no natural-language interface. | Bailey and Lopez de Prado, Deflated Sharpe Ratio ([SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)); Radon's own `tasks/timeseries-model-backlog.md` already mandates "backtest every feature against a dumb baseline". |
| (e) LoRA SFT on a 1B-9B open model, local | Format, schema adherence, house rules, tone, narrow classification; offline and private; cheap inference. | Poor at absorbing facts; forgets under repeated updates; small-data overfit; base model has memorized market history. | Gekhman et al.: LLMs "struggle to acquire new factual knowledge through fine-tuning" and as new-knowledge examples are learned they "linearly increase the model's tendency to hallucinate" ([EMNLP 2024](https://arxiv.org/abs/2405.05904)). Biderman et al.: LoRA "substantially underperforms full finetuning" on target-domain learning but "better maintains the base model's performance on tasks outside the target domain" ([arXiv 2405.09673](https://arxiv.org/abs/2405.09673)). |

### Data-size thresholds

The practitioner consensus and the vendor docs agree on rough bands. OpenAI's guide (still live) recommends starting with "50 well-crafted demonstrations" and expects visible improvement at 50-100 examples ([OpenAI SFT guide](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)). LIMA showed 1,000 curated examples suffice for alignment-style behaviour on a 65B model ([arXiv 2305.11206](https://huggingface.co/papers/2305.11206)). The MLX practitioner guide places 50-100 examples at "tone/format shifts only", 200-500 as the "practical sweet spot" for specialized tasks, and 500-1,000+ for complex, high-variability tasks ([InsiderLLM MLX guide](https://insiderllm.com/guides/fine-tuning-mac-lora-mlx/)). A 3B-7B SFT recipe paper found larger batch sizes with lower learning rates and that early loss dynamics predict final quality ([arXiv 2412.13337](https://arxiv.org/abs/2412.13337)).

Mapped to Radon: roughly 150 substantive eval reports and a few hundred rationale-bearing journal entries put decision traces in the "tone/format" to "sweet spot" band, not the "complex reasoning" band. Newsfeed tags (3,474 posts times 3 tags) are comfortably enough for a tagger.

### The finance-specific dangers

1. Regime learning. A model fine-tuned on a year of theses learns that year's regime. The financial sentiment literature shows "fine-tuned models suffer from general performance degradation in the presence of temporal distribution shifts" and that temporal shift "is prevalent in the financial text" ([EMNLP 2023, arXiv 2310.12620](https://arxiv.org/abs/2310.12620)). Radon's journal spans roughly Oct 2025 to now (cash-flow rows begin 2025-10 per `docs/performance-refactor-spec.md`), which is one regime.
2. Lookahead contamination of the base model. Lopez-Lira, Tang and Zhu show GPT-4o recalls S&P 500 closes within 1% for dates inside its training window, and prove that under memorization "the capacity of the model to forecast is non-identified" ([arXiv 2504.14765](https://ideas.repec.org/p/arx/papers/2504.14765.html)). Gao, Jiang and Yan's Lookahead Propensity test finds LLM forecast skill "collapses essentially to zero" after the cutoff ([arXiv 2512.23847](https://arxiv.org/abs/2512.23847)). Any open model released in 2026 has seen at least part of the period Radon's journal covers, so "held-out past decisions" are contaminated as an edge test (they remain valid as a format/gate test).
3. Continual-update forgetting. A 2026 mechanistic study across twenty models finds continual fine-tuning drives "systemic entropic dispersion" in early attention heads and "localized representation collapse" deeper, and that even the best subspace-regularized method recovers "up to 94.2%" of prior capability, not all of it ([arXiv 2601.18699](https://arxiv.org/abs/2601.18699)). "The Finetuner's Fallacy" argues domain data belongs in pretraining, not repeated fine-tuning, precisely because repetition at the fine-tuning stage invites overfitting and forgetting ([arXiv 2603.16177](https://arxiv.org/abs/2603.16177)). "SFT Memorizes, RL Generalizes" adds that SFT "tends to memorize training data and struggles to generalize out-of-distribution", while noting SFT is still needed to stabilize output format before any RL ([arXiv 2501.17161](https://arxiv.org/abs/2501.17161)).

Recommendation from section 1: fine-tune only for behaviour that is stable across regimes (format, gates, taxonomy, voice), never for facts and never for edge.

## 2. Realistic options for a solo operator with a Mac Mini and frontier API access

### Base models (verified on the model cards, September 2026)

| Model | Params | License | Context | Why it fits |
|---|---|---|---|---|
| [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) | 4B | Apache 2.0 | 262K native | Hybrid thinking on by default (disable for tagging); tool calling; the 0.8B-9B family launched Feb-Mar 2026 with the same architecture, so a tagger can start at 0.8B or 2B and a drafter at 4B or 9B. |
| [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) | 9B | Apache 2.0 | 262K | Upper bound of what a 32 GB Mac Mini trains comfortably with QLoRA. |
| [Gemma 4 E4B](https://ai.google.dev/gemma/docs/core/model_card_4) | 4.5B effective (8B with embeddings) | Apache 2.0 | 128K | Text, image and audio input; the image path matters because the newsfeed vision tagger currently runs on Claude Haiku 4.5 for posts with charts. Gemma 4 12B (256K) is the step up. |
| SmolLM3 3B, Phi-4 mini | 3B-4B | Apache 2.0 / MIT | 128K | Fine fallbacks; no advantage over Qwen3.5-4B for this workload. |

Llama 4 ships as Scout/Maverick MoE at sizes that do not fit this use case; skip it.

### Tooling

| Tool | Status on Apple Silicon | Notes |
|---|---|---|
| [mlx-lm](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md) | Native, first choice | `mlx_lm.lora --model <hf id> --train --data <dir> --iters 600`; supports `lora`, `dora`, `full`; JSONL in chat, prompt/completion or text form; `--mask-prompt` computes loss on assistant turns only; `mlx_lm.fuse` merges adapters. The docs' own datum: Mistral-7B LoRA at "about 250 tokens-per-second" on an M1 Max 32 GB with batch 1 and 4 layers. |
| [mlx-tune](https://github.com/ARahim3/mlx-tune) (formerly unsloth-mlx) | Community, v0.6 pre-release | Unsloth-compatible API over MLX: SFT, DPO, GRPO, ORPO, KTO, VLM SFT for Gemma 4 and Qwen3.5 vision, QLoRA. GGUF export needs a non-quantized base. Not affiliated with Unsloth. |
| Unsloth | Studio supports MLX per its [requirements page](https://unsloth.ai/docs/get-started/fine-tuning-for-beginners/unsloth-requirements); the core library is CUDA-first | Use it if you rent a GPU hour; not the Mac path. |
| [Axolotl](https://github.com/axolotl-ai-cloud/axolotl/issues/295) | Partial M-series support, documented constraints around MPS, bitsandbytes, DeepSpeed | Skip on Mac. |
| [HF TRL SFTTrainer](https://huggingface.co/docs/trl/sft_trainer) | Works via PyTorch MPS, slower than MLX | Useful as the dataset-format reference: TRL's conversational `messages` JSONL is the same shape mlx-lm accepts, so one dataset serves both. |
| Ollama / llama.cpp | Inference | `web/lib/llm/provider.ts` already routes to Ollama-style OpenAI-compatible bases. |

### Hardware and time

Memory per the MLX guide: LoRA on 8B needs about 14 GB, QLoRA about 7 GB; 14B needs 24 GB / 12 GB ([InsiderLLM](https://insiderllm.com/guides/fine-tuning-mac-lora-mlx/)). A 16 GB Mac Mini therefore handles QLoRA up to 8B; 32 GB handles LoRA on 9B and QLoRA on 12B-14B; 64 GB is unnecessary for this plan. Throughput numbers quoted in the same guide range 200-320 tok/s for 8B LoRA on M-series Pro/Max chips; an M4 Pro at 273 GB/s bandwidth sits in that band ([DEV Community, Mac chips 2026](https://dev.to/macyou/run-local-llms-on-a-mac-in-2026-which-chip-runs-which-model-and-why-bandwidth-beats-cores-501k)).

Estimate (mine, from those figures): a decision-trace set of 500 examples averaging 1,500 tokens is 750K tokens per epoch; at 250 tok/s that is 50 minutes per epoch on an 8B-9B model, roughly half on a 4B. Three epochs of a 4B drafter is an evening. A tagger on 0.8B-2B with 3,474 short examples trains in under an hour. Iteration count guidance from the guide: 300-600 iters for 200-500 examples, loss should flatten by 300-500.

### Building the SFT dataset from Radon's artifacts

Everything below is derivable from artifacts that already exist; connectors in `scripts/knowledge/sources/` already render most of it to text.

| Source (verified shape) | Volume (verified as of 2026-07-20) | Training pair | Task type |
|---|---|---|---|
| `journal` rows: `payload` JSON with `ticker`, `structure`, `action`, `decision`, `thesis`, `gates_passed`, `risk_factors`, `target_exit`, `stop_loss`, `contracts`, `fill_price`, `pct_of_bankroll` (`journal.py:_render_rationale`) | 819 rows, but rows with `decision == "IB_AUTO_IMPORT"` are bare fills; thesis-bearing entries are the minority. `data/trade_log.json` entries with a `thesis` add more (the plan mentions duplicate ranges 1-19 and 635-677, implying roughly 680 entries, many stubs). Realistic: 150-300 usable decision traces. | Input: ticker + structure + flow summary. Output: thesis, gates named, risk factors, exit and stop. | Decision trace |
| `reports/*.html` trade specs (10 required sections per `docs/reports.md`, chunked ~4,000 chars by `evals.py`) | 183 files minus `tweet-*` cards, minus files under 200 chars; realistic 120-160 substantive reports. | Input: milestone data (flow, options, context). Output: the full report in house format with four-gates summary and NO_TRADE handling. | Report drafting, format |
| `posts` table: `title`, `content`, `images`, `tags`, `tags_text`, `tags_vision` | 3,474 posts, exactly 3 UPPERCASE-KEBAB tags each (`newsfeed/CLAUDE.md`), so ~10,400 tag labels already produced by Haiku 4.5 (vision) and Cerebras gpt-oss-120b (text). | Input: post title + body (+ image for VLM). Output: 3 tags in taxonomy form. | Classification (distilled from machine labels, see caveat) |
| `docs/options-structures.{json,md}`, `docs/evaluation.md`, root `CLAUDE.md` gates and signal thresholds | Small, but can be expanded programmatically: synthetic gate cases (R:R below 2:1, Kelly above 2.5%, undefined risk) with the correct "stop, name the gate" answer. | Input: structure + numbers. Output: pass/fail with the named gate. | Rule compliance |
| `assistant_turns` (migration 0030/0065) | Per-turn telemetry: `user_msg` truncated to 200 chars, tool calls, outcome; assistant text is not stored. | Not usable as traces today; would need a provenance-aware full-transcript store, which is a design decision (real account figures). | n/a |

Caveat on the newsfeed labels: they are already machine outputs, so a local tagger is distilling a distilled label. The right teacher target is the merged `tags` column (post-`__normaliseTags`), with a 200-post operator-audited slice as the true test set.

### Distillation from the frontier model's own Radon outputs

This is the cheapest high-quality label source. The `evaluate` pipeline's milestone reasoning, produced by Claude in Claude Code sessions, is already the format the operator accepts. Two ways to get more of it: (1) harvest existing reports, (2) re-run past tickers through the pipeline with the flow snapshot frozen at the report date (the `scan_snapshots`, `gex_snapshots`, `vcg_snapshots` and `oi_changes` tables make this reproducible). Token cost is small: 3,474 posts at ~1,500 input tokens through Claude Haiku 4.5 ($1 per MTok input, $5 output) is about $5-8; 300 report regenerations at ~20K tokens each through Claude Sonnet 5 ($2 / $10 per MTok) is about $15-25 (prices from the Anthropic table cached 2026-06-24 in the claude-api skill).

Policy check, verified: Anthropic's help center states "Our Terms do not allow the use of Outputs to train models that are competitive with Anthropic's own" and, on the permitted side, "You can use Claude's Outputs to train models that don't compete with Anthropic's own", listing sentiment analysis, content categorization, summarization, information extraction and anomaly detection ([Anthropic support](https://support.claude.com/en/articles/12326764-can-i-use-my-outputs-to-train-an-ai-model)). A newsfeed tagger, a report formatter and a gate-compliance checker are squarely in the permitted list. A general "Radon chatbot" trained on Claude transcripts to replace the assistant loop moves toward the restricted case. That policy boundary coincides with the technically sound boundary: keep the distilled model narrow.

## 3. "Constantly studying my portfolio, news, trades": three different things

| What the operator means | What it actually is | Right mechanism | Radon status |
|---|---|---|---|
| (i) "Knows my positions, today's flow, this morning's news" | Knowledge that changes hourly or daily | Retrieval and tools, not weights. Positions come from `portfolio_snapshots` and IB sync; flow from `scan_snapshots` / `gex_snapshots` / UW; news from `posts` and `headlines_ring`. | Built. Hourly KB ingest, assistant tools, MCP. Gap: the golden set is still `draft: true` and the evaluate pipeline's `prior_context` hook (KB plan Phase 4) is unbuilt. |
| (ii) "Thinks like Radon: four gates, structure taxonomy, report shape, my voice, no rationalizing" | Skills, format, house rules, style | SFT / LoRA on curated traces; also achievable with prompt caching of the rules (Anthropic prompt caching, cache reads at a fraction of input price). | Partly in prompts. Not measured. |
| (iii) "Learns which signals work" | Predictive edge | Classic ML with walk-forward backtests, purged CV, deflated Sharpe; tiny sample discipline. | Harness exists (`backtest_runs`, `forecast_calibration`, Chronos-2). |

The failure mode to avoid is letting (i) or (iii) leak into (ii)'s training set. A decision trace that includes "NVDA at 182 with DPI 41%" teaches the model a stale fact and a regime; the same trace rewritten as "given DPI pace 1.3x prior and ask-dominant sweeps, gate 2 passes because..." teaches the reasoning pattern. The dataset builder should template facts as placeholders wherever the fact is retrievable at inference time.

### Proposed continual pipeline

```
hourly   radon-knowledge.timer      ingest -> distill -> embed -> upsert   (exists)
nightly  trace_builder.py           journal + reports + posts -> traces.jsonl (append, content_hash idempotent, facts templated)
weekly   eval_suite.py              run frozen eval set against: frontier+RAG baseline, current adapter, candidate adapter
monthly  mlx_lm.lora (LoRA, base frozen)   train on FULL accumulated trace set + replay, never the delta alone
         promote adapter only if: format >= prior, gate compliance >= prior, hallucination <= prior, held-out agreement not worse by > 1 s.e.
         else: keep prior adapter (adapters are small files; rollback = symlink)
```

Design rules and their sources:
- Base weights frozen, LoRA only: "forgets less" ([Biderman et al.](https://arxiv.org/abs/2405.09673)). Full fine-tuning on a 4B model is feasible on a Mac but buys nothing here and costs general capability.
- Retrain from scratch on the full accumulated set each cycle rather than continuing training on the new slice. Sequential continual fine-tuning is where the forgetting mechanisms in [arXiv 2601.18699](https://arxiv.org/abs/2601.18699) bite; a from-scratch LoRA on 500-1,000 examples takes an evening, so there is no reason to accept that risk. Mix in a general instruction replay slice (a few hundred generic chat examples) to hold format capability, which the replay literature reports as the simplest effective mitigation ([arXiv 2506.09428](https://arxiv.org/pdf/2506.09428)).
- Time-split, never random-split, evaluation. The last 60-90 days of traces are held out; the model never sees them. This is the same walk-forward discipline the timeseries backlog already imposes.
- Never train on news bodies as text (unsupervised continual pretraining). It is the weakest knowledge-injection method ([arXiv 2601.07054](https://arxiv.org/abs/2601.07054)) and the one most exposed to temporal drift ([arXiv 2310.12620](https://arxiv.org/abs/2310.12620)).
- Never train on P&L outcomes as labels for the LLM. See section 4.

## 4. Evaluation: measuring "tailored to Radon" honestly

Radon already has one honest eval: the golden-set hit@5 gate. The model eval should have the same shape: a frozen set, a stated gate, a JSON summary on stdout, a systemd oneshot. Five metrics, in decreasing order of trust.

| Metric | How | Automatable | Trust |
|---|---|---|---|
| Format adherence | Report has the 10 required sections; tags are exactly 3, match `^[A-Z0-9&-]+$`, dedup case-insensitively; NO_TRADE produces no structure or Kelly section. | Fully (regex + schema). | High |
| Gate compliance | Synthetic cases: 200+ programmatically generated structures with known R:R and Kelly fraction. Model must say PASS/FAIL and name the gate. Note that the gates are enforced in code (`OrderRiskGate`, `order_limits.py`); the model's job is to invoke and narrate, not to be the calculator. Score naming accuracy, not arithmetic. | Fully. | High |
| Hallucination rate on Radon facts | Closed-book questions from the golden set ("what was the EWY risk-reversal thesis?"). Correct behaviour for a small model is to call `search_knowledge`, not answer. Score: fabricated ticker, date or number per 100 answers, with and without retrieval. | Mostly (LLM-as-judge with a frontier model against the KB doc). | Medium-high |
| Held-out decision agreement | Time-split last 60-90 days: does the model reach the same milestone-4 PASS/FAIL and the same structure family as the operator did? Report with a binomial confidence interval. With 30 held-out decisions a 70% agreement rate has a 95% interval of roughly 51-85%, so the metric is a trend indicator, not a verdict. | Semi (structured compare). | Medium; small n |
| P&L-linked | Only through the existing backtest harness: the model's PASS/FAIL as a filter over a fixed universe, walk-forward, with purged CV and embargo, reported as a deflated Sharpe with the number of trials declared ([Bailey and Lopez de Prado](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)). | Yes, but interpret with humility. | Low until n is in the hundreds of independent decisions per regime |

What not to claim:
- Not "the model learned my edge". With a few hundred decisions in one regime, any P&L lift is indistinguishable from noise and selection bias; DSR exists precisely because "high simulated performance is easily achievable after backtesting relatively few strategy configurations" ([Bailey and Lopez de Prado](https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf)).
- Not "it predicted past trades correctly" for any date inside the base model's training window; per the memorization results ([Lopez-Lira et al.](https://ideas.repec.org/p/arx/papers/2504.14765.html), [Gao et al.](https://arxiv.org/abs/2512.23847)) forecast skill on that period is non-identified. Use pre-cutoff decisions for format and gate scoring only.
- Not "it knows my portfolio" from weights. If a closed-book question about a position is answered without a tool call, that is a defect, not a feature.
- Not "better than Claude". The comparison that matters is task-specific: tagger agreement with the audited slice, report format pass rate, gate naming accuracy. On open-ended analysis a 4B model will lose to the frontier model plus RAG, and the eval should show it.

## 5. Recommendation: phased plan, costs, and what would not be useful

### Recommendation in one paragraph

Do Phase 0 now: it is cheap, it hardens the existing RAG, and it makes every later claim measurable. Do Phase 1 as a bounded experiment because privacy, offline operation and independence from a third-party tagger have some value even though the dollar saving is trivial. Do Phase 2 only if Phase 1's eval harness shows the distilled model holding format and gate compliance at or above the frontier baseline. Keep Phase 3 in the classic-ML lane where it already lives. Do not build a continual weight-update loop on news, positions or P&L.

### Phase 0: dataset and baseline (1-3 days, $0 compute, under $30 API)

1. `scripts/knowledge/traces.py` (new): render journal rationale rows, trade_log theses and substantive reports into `data/traces/traces.jsonl` in TRL/mlx-lm conversational format; template retrievable facts; content-hash idempotent; time-stamped for splits. Reuse `sources/journal.py` and `sources/evals.py` rendering.
2. Curate the golden set out of `draft: true` (the KB plan already lists this as an operator follow-up). Add 20-30 closed-book hallucination probes.
3. Audit 200 newsfeed posts' tags by hand; that is the tagger test set.
4. Generate 200+ synthetic gate cases from `docs/options-structures.json`.
5. Run the frontier + RAG baseline through the full eval suite and record it. This number is the bar every adapter must clear.

Deliverable: `eval_suite.py` producing `{format, gates, hallucination, agreement}` JSON, plus the baseline row.

### Phase 1: distilled "Radon analyst" for tagging and report drafting (1-2 days, a few Mac hours, $10-30 API)

- Tagger: Qwen3.5-0.8B or 2B, LoRA via mlx-lm, 3,474 examples, target the merged `tags` column; VLM variant on Gemma 4 E4B via mlx-tune only if text-only tagging fails the audited slice. Serve through Ollama behind `provider.ts`. Acceptance: agreement with the audited slice within 3 points of the current pipeline.
- Drafter: Qwen3.5-4B (or 9B on 32 GB), LoRA, 150-300 report traces plus gate cases plus a replay slice. Acceptance: format adherence and gate naming at or above the frontier baseline; hallucination probes answered by tool call.
- Cost reality, stated plainly: the vision tagger costs about $0.003 per post (`scripts/newsfeed/CLAUDE.md`); at the observed ~20-25 posts per day (3,474 over roughly five months) that is under $30 a year. Report drafting through the frontier model is maybe a few dollars per evaluation. The local model does not pay for itself in API spend. Its value is latency, privacy for real account figures, and an offline fallback when the Cerebras or Anthropic key is missing (the newsfeed already needs one of the two).

### Phase 2: decision-trace SFT with held-out eval (2-3 days, monthly thereafter)

- Add the templated decision traces to the drafter's training set; retrain from scratch monthly on the full set; time-split hold-out of the last 60-90 days; promotion and rollback rule from section 3.
- Wire the adapter into the assistant as a "draft" tool the frontier model can call, not as a replacement for the assistant loop. The frontier model plus RAG stays the decision surface; the small model formats and pre-fills.
- Report agreement with confidence intervals every cycle. Expect it to be noisy for at least a year of accumulation.

### Phase 3: signal models, separately (existing lane)

Flow-surprise residuals, regime bands and the vol cone belong to Chronos-2 / gradient boosting with the `backtest_runs` harness, deflated Sharpe and a dumb-baseline gate, exactly as `tasks/timeseries-model-backlog.md` prescribes. Nothing in this report changes that plan except one addition: any LLM-derived feature (a tag, a sentiment, a PASS/FAIL) entering a backtest must be produced by a model whose training cutoff predates the backtest window, or the walk-forward is contaminated.

### What would not be useful

- Continual weight updates on daily news, positions or flow. Slower and worse than the hourly KB ingest that already exists; documented forgetting and drift risks.
- Fine-tuning a frontier model via API in 2026. OpenAI is winding its platform down; Anthropic's path is Claude 3 Haiku on Bedrock only; FineTuneBench shows the approach barely injects knowledge anyway.
- Training an LLM on P&L outcomes to "learn what works". Sample size, regime coverage and lookahead contamination make any result non-identifiable.
- Full fine-tuning, or anything above 9B on a Mac Mini. More forgetting, more time, no measurable benefit at this data scale.
- A general Radon chatbot distilled from Claude transcripts to replace the assistant loop. Quality regression on open-ended analysis and tool use, and it drifts toward the "competing model" clause of the provider terms.
- Renting GPUs. Nothing in this plan needs more than the Mac.

## Sources

Radon repository (verified, read-only): `CLAUDE.md`, `scripts/CLAUDE.md`, `docs/evaluation.md`, `docs/reports.md`, `scripts/newsfeed/CLAUDE.md`, `scripts/db/migrations/0001_init.sql`, `0015_forecast_snapshots.sql`, `0016_forecast_calibration.sql`, `0018_backtest_runs.sql`, `0028_knowledge.sql`, `0030_assistant_turns.sql`, `0065_assistant_turns_provenance.sql`, `scripts/knowledge/{ingest,distill,embed,eval_golden,golden_set.json}`, `scripts/knowledge/sources/{journal,evals,newsfeed}.py`, `tasks/knowledge-base-plan.md`, `tasks/timeseries-model-backlog.md`, `web/lib/llm/provider.ts`, `docs/cloud-services.md` (backup drill: 37 tables / 80,171 rows, 2026-06-12).

External:
- Ovadia et al., Fine-Tuning or Retrieval? Comparing Knowledge Injection in LLMs: https://arxiv.org/abs/2312.05934
- Fine-Tuning vs. RAG for Multi-Hop QA with Novel Knowledge (Jan 2026): https://arxiv.org/abs/2601.07054
- FineTuneBench, commercial fine-tuning APIs and knowledge infusion: https://arxiv.org/abs/2411.05059
- Gekhman et al., Does Fine-Tuning LLMs on New Knowledge Encourage Hallucinations? (EMNLP 2024): https://arxiv.org/abs/2405.05904
- Biderman et al., LoRA Learns Less and Forgets Less: https://arxiv.org/abs/2405.09673
- Chu et al., SFT Memorizes, RL Generalizes: https://arxiv.org/abs/2501.17161
- Mechanistic Analysis of Catastrophic Forgetting During Continual Fine-tuning (2026): https://arxiv.org/abs/2601.18699
- The Finetuner's Fallacy (2026): https://arxiv.org/abs/2603.16177
- Improved SFT to Mitigate Catastrophic Forgetting (replay): https://arxiv.org/pdf/2506.09428
- Temporal distribution shift in financial sentiment classification (EMNLP 2023): https://arxiv.org/abs/2310.12620
- Lopez-Lira, Tang, Zhu, The Memorization Problem: https://ideas.repec.org/p/arx/papers/2504.14765.html
- Gao, Jiang, Yan, Detecting Lookahead Bias in LLM Forecasts: https://arxiv.org/abs/2512.23847
- Bailey and Lopez de Prado, The Deflated Sharpe Ratio: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- LIMA: Less Is More for Alignment: https://huggingface.co/papers/2305.11206
- Unveiling the Secret Recipe: SFT for Small LLMs: https://arxiv.org/abs/2412.13337
- OpenAI supervised fine-tuning guide (wind-down notice, 50-100 example guidance): https://developers.openai.com/api/docs/guides/supervised-fine-tuning
- OpenAI RFT billing ($100 per training hour): https://help.openai.com/en/articles/11323177-billing-guide-for-the-reinforcement-fine-tuning-api
- Anthropic, Fine-tune Claude 3 Haiku in Amazon Bedrock: https://claude.com/blog/fine-tune-claude-3-haiku
- Anthropic, Can I use my Outputs to train an AI model?: https://support.claude.com/en/articles/12326764-can-i-use-my-outputs-to-train-an-ai-model
- Anthropic pricing: https://docs.claude.com/en/docs/about-claude/pricing
- mlx-lm LoRA documentation: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md
- InsiderLLM, Fine-Tuning on Mac: LoRA and QLoRA with MLX: https://insiderllm.com/guides/fine-tuning-mac-lora-mlx/
- mlx-tune (Unsloth-compatible MLX trainer): https://github.com/ARahim3/mlx-tune
- Unsloth requirements: https://unsloth.ai/docs/get-started/fine-tuning-for-beginners/unsloth-requirements
- Axolotl Apple Silicon issue: https://github.com/axolotl-ai-cloud/axolotl/issues/295
- HF TRL SFTTrainer: https://huggingface.co/docs/trl/sft_trainer
- Qwen3.5-4B model card: https://huggingface.co/Qwen/Qwen3.5-4B
- Qwen3.5-9B model card: https://huggingface.co/Qwen/Qwen3.5-9B
- Gemma 4 model card: https://ai.google.dev/gemma/docs/core/model_card_4
- Mac chips and bandwidth for local LLMs (2026): https://dev.to/macyou/run-local-llms-on-a-mac-in-2026-which-chip-runs-which-model-and-why-bandwidth-beats-cores-501k
- Fine-tuned compact models vs GPT on classification (Springer): https://link.springer.com/chapter/10.1007/978-3-032-25314-9_5

## Open questions for the operator

1. Mac Mini memory: 16, 24, 32 or 64 GB? It decides 4B-LoRA versus 9B-LoRA versus 12B-QLoRA.
2. How many journal rows carry a real `thesis` versus `IB_AUTO_IMPORT` stubs? One read-only count on Turso settles whether Phase 2 has 150 or 400 traces.
3. Should full assistant transcripts be stored (with the R-454/R-457 provenance rules) so operator Q&A becomes a trace source? Today `assistant_turns` keeps only 200 characters of the user message.
4. Is privacy of real account figures a strong enough reason on its own to run a local tagger and drafter, given the API saving is under $100 a year?
5. Which surfaces, if any, should ever call the small model directly rather than through the frontier model as a tool? The recommendation is none for decisions.
6. Golden set curation is still `draft: true` since 2026-07-18; Phase 0 depends on it.
