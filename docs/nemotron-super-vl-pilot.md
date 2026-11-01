# Hosted Nemotron 3.5 Super VL pilot

Super VL (`nvidia/nemotron-3.5-super-vl-120b-a12b`) became the default NVIDIA
vision model on 2026-11-01, ahead of the gates below: the prior chain was not
callable on the production key (Llama 11B answered 404 for the account, Llama
90B timed out on every production call over 24h). Llama 90B is now the
fallback. The gates below remain the quality check still owed. Provider order,
PDF parser, embeddings, web assistant and Qwen GPU adapters are unchanged.

## Request contract

Only Super VL and Nemotron 3 Ultra (the text default), when called through the
NVIDIA provider, get `reasoning_effort: "none"` and API `stream: false`. Small extraction budgets
are reserved for final answers. HTTP transport reading remains bounded and
streamed internally; the response is parsed as one JSON document, not SSE.
The Llama 90B fallback and other providers receive no new fields.
Existing array/object parsing, semantic validators, rate limits, response
byte limits and provider fallbacks remain in effect. No JSON schema is forced:
CTA extraction returns arrays while research review returns objects.

References: [hosted API](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3_5-super-vl-120b-a12b-infer),
[model-specific limitations](https://docs.nvidia.com/nim/large-language-models/latest/get-started/advanced/get-started-nemotron-3.5-super-vl.html).

## Enable a bounded pilot

1. Confirm NVIDIA account access with synthetic image/text requests from an
   isolated evaluation environment. Keep the existing NVIDIA key handling;
   do not log or copy credentials into an evaluation artifact.
2. Set only `NVIDIA_VISION_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b` in that
   environment. Call the NVIDIA rung directly during comparison so earlier
   providers cannot mask its results. Keep rate pacing enabled for real calls.
3. Compare at least 100 reviewed CTA/table images and 100 research page/crop
   cases against the current NVIDIA vision model on identical frozen inputs.
   Record exact numeric/sign/unit/date accuracy, omitted cells, abstentions,
   crop completeness, false approvals, p95 latency, failures and quota impact.
   Include approximate values, bounds and instruction-like source text.
4. Require zero observed critical numeric errors, no coverage regression and
   no increase in research false approvals; human-review disagreements. These
   are sample acceptance gates, not a guarantee of error-free production.
5. After passing those gates, set `NVIDIA_VISION_MODEL` in `/etc/radon/env`,
   regenerate the runtime environment using the existing deployment procedure,
   and restart `radon-model-gateway.service`. Confirm sanitized winner/model
   logs from the gateway. Existing provider ordering still determines whether
   NVIDIA is called.

The gateway environment admits `NVIDIA_VISION_MODEL`, `NVIDIA_TEXT_MODEL`, and
`NVIDIA_MODEL`. It does not admit arbitrary `NVIDIA_*` settings, and the
newsfeed container does not receive those names. Editing a host environment
file alone does not update an already running process or a previously rendered
container environment.

Text remains a separate evaluation. If needed, explicitly set
`NVIDIA_TEXT_MODEL` to the same hosted ID in an evaluation environment, then
measure taxonomy accuracy, grounded summaries and latency against the current
text model. Do not use the broad `NVIDIA_MODEL` override for a vision-only pilot.
The short tagging/distillation budgets are unchanged.

## Rollback and limits

Set `NVIDIA_VISION_MODEL=meta/llama-3.2-90b-vision-instruct` to bypass Super VL; regenerate the affected runtime environment and restart the
affected consumer. A code rollback is the ordinary prior-release rollback.
No data migration or re-embedding is needed.

This does not change nightly CLI model discovery: it still prefers the largest
catalog model and needs a separate explicit-selection change before a CLI pilot.
Never point `RADON_RESEARCH_PARSE_MODELS` at Super VL: the specialized Parse
adapter expects a different output contract. Hosted entitlement, production
service terms, latency, cost and real chart quality must be measured before
promotion. No live workload evaluation is claimed by mocked regression tests.
