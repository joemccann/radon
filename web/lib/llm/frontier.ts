/**
 * The one compiled-in frontier model per provider, shared by the two halves of
 * the model picker.
 *
 * `provider.ts` calls each provider with this id when nothing else names one
 * (no per-turn pick from the picker, no `<PROVIDER>_MODEL` env pin), and
 * `catalog.ts` serves the same ids as its BUILTIN_FRONTIER tier. They must
 * agree: a catalog offering `grok-4.6` while provider.ts still defaults xAI to
 * the withdrawn `grok-4` runs turns on a model the picker never showed, and
 * `grok-4` reportedly resolves to `grok-4.3` and bills as `grok-4.3` rather
 * than 404ing, so the drift would be silent.
 *
 * It lives in its own leaf module, importing nothing, so `catalog.ts` can read
 * it without pulling in `provider.ts` (which tests routinely replace wholesale
 * with a `chat` stub) and without `provider.ts` pulling in `catalog.ts`
 * (server-only: filesystem + Turso).
 *
 * Every id is bare, undated and GA. OpenAI is deliberately gpt-5.5, not a
 * gpt-5.6 tier: GPT-5.6 access is still per-org, and a default the key cannot
 * call turns a degraded path into a hard 404.
 *
 * Verified against the live provider model lists 2026-08-29.
 */
export const DEFAULT_MODELS = {
  anthropic: "claude-opus-5",
  openai: "gpt-5.5",
  xai: "grok-4.6",
} as const;
