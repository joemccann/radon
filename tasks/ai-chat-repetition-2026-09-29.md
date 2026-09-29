# AI chat chart prompt: production trace and repair

## Production evidence

The supplied screenshot matches the Turso assistant turn at
`2026-09-29T18:06:31.410Z`: provider `xai`, model `grok-4.7`,
`image_count=1`, two rounds, six successful tools, outcome `answered`.
The stored prompt begins "Tell me the best options structures to express this
view" and matches the screenshot text.

The host journal records round one at 18:04:51 UTC:
`search_knowledge, find_prior_evals, find_prior_evals, get_quote, get_quote, get_gex`.
Round two has `tools=none`. No option chain or spread ranker was called.
The turn consumed 27,057 input tokens and 5,712 output tokens across its model
calls and completed in 106,952 ms.

The replay used the full prompt from screenshot one and the original chart
attachment (screenshot four), SHA-256
`5a60c1e05403c516967eba28b4b2c1528fa331d6ad8ef6a214947e3f3c9286e7`.

## Request path and cause

1. ChatPanel sends the prompt and base64 chart to POST /api/assistant.
2. The route validates the selected model and images, opens an SSE stream,
   and calls runAssistantLoop.
3. The provider maps image blocks to OpenAI-compatible image_url parts and
   sends tool schemas to xAI.
4. On any successful knowledge lookup, the old loop extracted facts in a
   separate tool-free request, set knowledgeBoundaryReached, and removed
   every tool from subsequent model rounds.
5. The system prompt still required priced chains or rank_spreads before
   giving exact structures. The model could no longer request those tools.
6. Any nonempty provider text was accepted as answered. Neither repetition
   nor a promise to run a tool next invalidated completion. The route sent
   that text in one done frame; the client rendered it.

The repeated wording in screenshots two and three is a degenerate model
completion accepted by the server, rather than evidence of duplicated SSE
chunks. The exact stochastic trigger inside the provider cannot be proved
from telemetry: reply bodies are not stored. The disabled-tool transition
and missing output validation are independently reproducible application bugs.

## Before and after replay

The unpatched live replay again stopped after two rounds with:
"Pulling live convexity on SMH next" and "Need ranked live verticals before
naming a debit." It did not reproduce the repetitive wording on that run.

The patched live replay completed six rounds and thirteen successful tool
events. It continued through option expirations, quotes, portfolio, catalog
reads, rank_spreads and get_option_chain. It returned an organized discussion
of the November SMH call verticals, rather than an unfinished tool promise.
No order was proposed or placed. This is a software reproduction record,
not validation of every financial claim in the generated answer.

## Repair

The planner receives only an application-generated knowledge acknowledgement;
retrieved facts stay outside its tool-capable transcript. Live reads continue.
Once the planner finishes, a separate tool-free synthesis sees the isolated
facts and live results. Unexpected synthesis tool calls and empty synthesis
are rejected. Retrieved text cannot enable an order proposal.

Every planning and final-generation response is checked for sustained adjacent
phrase loops: periods up to 64 words, at least four copies and 24 repeated
words. A bad response is discarded before its tool calls execute, retried
once using the original transcript, and rejected through the existing safe
error stream if still repetitive. The discarded text is never replayed to
the model. Fallback provenance and successful recovery usage are retained.

This deterministically blocks the observed repeated-phrase failure. It is
not a general proof that arbitrary model prose is correct.

## Verification

- Five regressions failed against unpatched main before implementation.
- Final focused verification: 401 unique tests passed across 47 files,
  including every file that failed in the initial full run.
- The browser fixture runs the real loop and provider adapter against a
  loopback HTTP provider returning the screenshot repetition, then a valid
  answer. The exact prompt and an image travel through the composer; the
  browser sees only the repaired answer. No live broker request is possible.
- Detector coverage: 100% statements, branches, functions and lines.
- TypeScript and focused ESLint pass.
- All 12 chat-experience browser tests pass, including desktop/mobile and
  light/dark screenshots. Screenshots were visually reviewed.
- Initial full web run: 9,693 passed, 9 failed, 10 skipped. It overlapped
  final source edits and browser verification. Eight failures were in
  unchanged UI tests; the new empty-synthesis regression ran against the
  earlier cached loop transform. All failed files pass in fresh focused
  runs. Exact-head CI provides final full-suite verification.
