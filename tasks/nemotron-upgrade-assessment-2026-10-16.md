# Nemotron upgrade assessment

Reviewed October 16, 2026. Scope: repository configuration and NVIDIA's current public documentation. No production settings changed, credentials inspected, inference requests sent, or local test suites run. The supplied announcement was treated as reference material; its calls to action were not instructions.

## Recommendation

**Pilot hosted Nemotron 3.5 Super VL for the existing NVIDIA vision rung first. Evaluate text separately. Keep the specialized PDF parser, current embeddings, and trained GPU tagger in place.**

The immediate opportunity is chart and document-image understanding in MenthorQ extraction and research verification. Those paths still use Llama 3.2 Vision through NVIDIA. Switching the NVIDIA rung preserves the existing provider ladder; it does not make NVIDIA the primary provider or change deterministic trading gates.

This is a compatibility and suitability assessment, not measured evidence that the new model improves Radon. Release benchmarks do not establish financial-chart accuracy, latency, or cost for our workload.

## What shipped

NVIDIA announced Super VL on October 15. The release combines text, image and video understanding with reasoning and tool use. NVIDIA also announced Bolt 74B-A9B for smaller hardware; that is a separate deployment candidate, not the Super VL checkpoint in the attachment. [Official announcement](https://blogs.nvidia.com/blog/nemotron-3-5-super-vl/).

Super VL is a hybrid Mamba/Transformer mixture-of-experts model with 120B total and 12B active parameters, a vision encoder, and configurable reasoning. The BF16 reference checkpoint supports up to 1M tokens and commercial use under OpenMDW-1.1. Its published results include OCR-Reasoning 65.08, CharXiv reasoning 80.82, and MMLongBench-Doc 62.82; these are vendor evaluations, not Radon results. [NVIDIA model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16).

The hosted API identifier is `nvidia/nemotron-3.5-super-vl-120b-a12b`, using the same `https://integrate.api.nvidia.com/v1` base URL and `NVIDIA_API_KEY` as our current client. Build currently advertises **262K context**, rather than the checkpoint's 1M maximum, and labels the endpoint early access. Treat the hosted service's limits as authoritative for that deployment. NVIDIA's page offers a free prototype endpoint; account entitlement, production service terms and paid pricing remain unverified. [NVIDIA Build](https://build.nvidia.com/nvidia/nemotron-3.5-super-vl-120b-a12b).

The advertised throughput gains concern NVIDIA's serving configuration and benchmark workload. They are not an estimate of end-to-end Radon speed or savings. [Technical announcement](https://developer.nvidia.com/blog/nvidia-nemotron-3-5-super-vl-powers-specialized-agents-with-multimodal-reasoning/).

## Where Radon uses NVIDIA

These are checked-out code defaults, not assertions about the effective configuration of every deployed process.

| Workload | Current implementation | Decision |
|---|---|---|
| MenthorQ CTA/table image extraction | `scripts/clients/menthorq_client.py:1497` calls the vision cascade. `scripts/clients/model_ladder.py:108` defaults to `meta/llama-3.2-90b-vision-instruct`, then 11B. `NVIDIA_VISION_MODEL` overrides the primary model. | **First pilot.** Compare exact numeric rows, signs, ticker labels, omitted cells and abstentions. |
| Research page/crop and claim review | `scripts/research/model.py:51` uses the shared ladder. Image requests select the NVIDIA vision model; text-only selection uses the text model. | **First pilot alongside CTA.** Preserve source grounding, crop-completeness checks and rejection gates. |
| Newsfeed tagging, knowledge distillation and other text JSON calls | `scripts/clients/model_ladder.py:111` defaults to `nvidia/nemotron-3-super-120b-a12b`; overrides are `NVIDIA_TEXT_MODEL` then `NVIDIA_MODEL`. Distillation has an 800-token, 20-second budget in `scripts/knowledge/distill.py`. | **Second-stage comparison.** Reasoning may hurt short classification and summary latency. Consider Lightning only as a separate measured speed candidate. |
| Research PDF layout/OCR | `scripts/research/nemotron_parse.py:53` uses `nvidia/nemotron-parse`; v1 expects `markdown_bbox` tool output. The adapter recognizes Parse 2 explicitly and treats other names as v1. | **Keep.** Super VL is not a compatible model-name replacement for this contract. A future alternative needs a dedicated adapter and geometry evaluation. |
| Knowledge retrieval | `scripts/knowledge/embed.py:41` uses `nvidia/nemotron-3-embed-1b`, 2048 dimensions, with local 384-dimensional fallback. | **Keep for this rollout.** Super VL generates text, not retrieval vectors. |
| Nightly NVIDIA CLI agent | `scripts/agent_cli_bootstrap.sh:100` ranks NVIDIA catalog entries by parameter count; live discovery wins over provisioned/fallback models. | **Explicit selection work required.** Re-running bootstrap or changing the fallback does not reliably select Super VL. |
| GPU-host tagger/distill adapters | `cloud/gpu/runtime.py:45` pins Qwen2.5-1.5B-Instruct; deployment documentation records a 24GB RTX PRO 4000. | **Keep.** Existing adapters require their trained base. Super VL is a different infrastructure project. |
| Web assistant | `web/lib/llm/catalog.ts:37` exposes Anthropic, OpenAI and xAI. | **No existing NVIDIA integration to upgrade.** Adding one would be a separate feature. |

The existing order favors subscription providers before NVIDIA, with Cerebras last. A better NVIDIA fallback only affects requests that reach that rung. Evaluate it directly in an isolated harness so other providers cannot mask its performance.

## Required compatibility work

1. **Control reasoning explicitly.** The hosted API documents `reasoning_effort` values `none`, `low`, and `high`, with high as the default. Begin factual row extraction and tagging with `none`; compare `low` for research judgments. The API supports structured output via JSON schema. These settings need to reach the NVIDIA request builders, not just an environment variable. Scope new controls to the Super VL model: the retained Llama 11B fallback may reject Nemotron-specific fields. [API reference](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3_5-super-vl-120b-a12b-infer).

2. **Keep final answers distinct from reasoning.** Current shared builders do not set NVIDIA-specific reasoning or output-format controls. Radon extracts final `message.content`; reasoning alone cannot satisfy the JSON contract. Existing vision calls allow 4096 tokens, research calls commonly 6000, and distillation 800. Record finish reason, empty final content and latency. Do not blindly increase every call's budget. Also explicitly request `stream: false` in the NVIDIA API body for the initial pilot: Radon's transport-level `stream=True` reads bounded HTTP chunks, then parses one JSON response; it does not implement NVIDIA token/SSE streaming. The new endpoint documents streaming as its default. Enabling token streaming requires a separate SSE adapter.

3. **Constrain and validate output.** Use each workload's actual schema: the CTA path expects a JSON array of rows, while research and tagging expect objects. NVIDIA documents that JSON-object mode can omit required fields and that reasoning can exhaust the completion budget before any final answer. Prefer schema constraints plus existing semantic validators. Do not use `thinking_token_budget` as an answer guarantee. [Model-specific limitations](https://docs.nvidia.com/nim/large-language-models/latest/get-started/advanced/get-started-nemotron-3.5-super-vl.html).

4. **Propagate settings into the right processes.** The newsfeed environment filter at `cloud/scripts/radon-app-runtime.sh:678` includes `NVIDIA_API_KEY` but excludes `NVIDIA_TEXT_MODEL`, `NVIDIA_VISION_MODEL`, and `NVIDIA_MODEL`. An env-only host change does not reliably change that container. Allowlist only the intended controls and verify the effective model identifier in sanitized telemetry after restart.

5. **Preserve pacing and fallbacks.** `scripts/nvidia_rate_limit.py` defaults to 20 requests/minute per host and shares pacing across parsing, embeddings, ladder calls and nightly CLI traffic. This is a repository setting, not a newly verified account quota. A second host has its own limiter; combined traffic still shares the account key. A faster model does not remove this bottleneck. Retain authorization failure handling, cooldowns and fallback behavior.

## Upgrade configuration and rollout

The initial vision candidate setting is:

```dotenv
NVIDIA_VISION_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b
```

Do not set the broad `NVIDIA_MODEL` override for the initial pilot. Keep the text default unchanged. For the later text comparison, use the separate candidate setting:

```dotenv
NVIDIA_TEXT_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b
```

These are proposed settings, not applied changes. The vision fallback remains the current 11B model unless deliberately revised. The research `model_override` is Anthropic-specific; use the NVIDIA vision setting for NVIDIA image requests.

Dependency graph: U1 -> U2 -> U3 -> U4; U3 -> U5.

- **U1 depends_on: []** Confirm account access with synthetic text/image inputs; verify accepted reasoning controls, final JSON with API `stream: false`, bounded transport reading and structured outputs. Avoid sending private research during this first probe.
- **U2 depends_on: [U1]** Add focused NVIDIA request controls and any required environment propagation, with mocked regression coverage. Keep provider ordering and existing validation unchanged.
- **U3 depends_on: [U2]** Run GitHub-hosted evaluation over a frozen, reviewed set of at least 100 CTA/chart examples and 100 research page/crop cases. Include small text, negative values, units, dates, clipped charts, approximate/bounded numbers and misleading document instructions. Compare the old NVIDIA vision model and Super VL on identical inputs. Keep original evidence immutable.
- **U4 depends_on: [U3]** Promote only if accepted numeric outputs have zero observed critical errors, total extraction coverage does not regress, research false approvals do not increase, and measured p95 latency, failure rate and quota impact fit existing budgets. Require human adjudication of disagreements. Zero errors in this sample is a release gate, not proof of zero production risk. Canary one workflow, then broaden. Pass exact-head CI before shipping.
- **U5 depends_on: [U3]** Separately compare text tagging/distillation with reasoning disabled against the current text model, using taxonomy accuracy, grounded summary quality, schema success, p95 latency and retries. Promote text only if it wins its own evaluation.

Rollback: restore the previous per-workload model setting, regenerate any filtered runtime environment and restart only affected consumers. Retain the existing parser and retrieval indexes throughout. For code changes, retain the prior release as the deployment rollback target.

## Separate follow-ups

NVIDIA also now lists **Nemotron 3.5 Embed 1B**, which accepts text and document images and emits 2048-dimensional vectors. That makes it a separate retrieval experiment worth tracking. Matching dimensions do **not** mean matching vector spaces: it would require re-embedding passages and queries into a separately identified index, retrieval evaluation and coverage checks before switching. Hosted entitlement/API compatibility were not established. [NVIDIA embedding model card](https://huggingface.co/nvidia/Nemotron-3.5-Embed-1B-BF16).

Self-hosting Super VL is not justified by this assessment. NVIDIA's NVFP4 checkpoint has documented multi-GPU deployment recipes, including two H100s for up to 1M context; our documented 24GB tagger host is not an equivalent target. Bolt may merit a distinct local-hardware benchmark later, but cannot reuse the Qwen adapters. [NVFP4 deployment card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-NVFP4).

Evidence still required before deployment: effective production settings, account access, workload quality/latency measurements, quota impact and production pricing/service terms. No throughput, savings or quality gain is claimed as measured.
