import { createHash } from "node:crypto";
import { alternateProvider, chat, resolveProvider, type LlmProviderName } from "@/lib/llm/provider";
import { NEWSFEED_VOICE_SYSTEM, parseVoiceCopy, type NewsfeedVoiceCopy, type NewsfeedVoiceInput } from "@/lib/newsfeedVoice";
import { VOICE_REWRITE_BUDGET_MS, type VoiceStage } from "@/lib/newsfeedVoiceProgress";

type Provider = Exclude<LlmProviderName, "grok">;
type RewriteOptions = { signal: AbortSignal; onStage: (stage: VoiceStage) => void };

/** A primary still silent after this long gets raced by the alternate subscription. */
export const HEDGE_DELAY_MS = 15_000;
/** The post is capped near 400 characters, so the model only needs the article's lead. */
export const MODEL_SOURCE_CHARS = 6_000;
const MAX_TOKENS = 1_600;
const CACHE_TTL_MS = 12 * 60 * 60 * 1000;
const CACHE_MAX_ENTRIES = 200;

const verifiedDrafts = new Map<string, { copy: NewsfeedVoiceCopy; expiresAt: number }>();

/** Test seam. */
export function resetVoiceRewriteCache(): void { verifiedDrafts.clear(); }

function cacheKey(input: NewsfeedVoiceInput): string {
  return createHash("sha256").update(JSON.stringify([input.title, input.content])).digest("hex");
}

function cachedDraft(key: string): NewsfeedVoiceCopy | undefined {
  const hit = verifiedDrafts.get(key);
  if (hit && hit.expiresAt > Date.now()) return hit.copy;
  verifiedDrafts.delete(key);
  return undefined;
}

function rememberDraft(key: string, copy: NewsfeedVoiceCopy): void {
  if (verifiedDrafts.size >= CACHE_MAX_ENTRIES) verifiedDrafts.delete(verifiedDrafts.keys().next().value!);
  verifiedDrafts.set(key, { copy, expiresAt: Date.now() + CACHE_TTL_MS });
}

function rewriteProviders(): Provider[] {
  const primary = resolveProvider();
  const alternate = alternateProvider(primary);
  return alternate ? [primary, alternate] : [primary];
}

/** One provider's draft, accepted only once it passes the numeric-fidelity check. */
async function verifiedAttempt(provider: Provider, input: NewsfeedVoiceInput, signal: AbortSignal, onStage: RewriteOptions["onStage"]) {
  const result = await chat({
    provider,
    fallback: false,
    system: NEWSFEED_VOICE_SYSTEM,
    messages: [{ role: "user", content: JSON.stringify({ title: input.title, content: input.content.slice(0, MODEL_SOURCE_CHARS) }) }],
    maxTokens: MAX_TOKENS,
    reasoningEffort: "low",
    timeoutMs: VOICE_REWRITE_BUDGET_MS,
    signal,
  });
  onStage("checking");
  return parseVoiceCopy(result.text, input);
}

/**
 * Hedged race: the primary starts now; the alternate starts when the primary
 * fails (a provider error or an unverifiable draft) or stays silent past
 * HEDGE_DELAY_MS. The first verified draft wins and the loser is aborted, so
 * a slow or degraded provider costs at most the hedge delay, not the budget.
 */
function raceProviders(providers: Provider[], input: NewsfeedVoiceInput, { signal, onStage }: RewriteOptions): Promise<NewsfeedVoiceCopy> {
  const loserAbort = new AbortController();
  const attemptSignal = AbortSignal.any([signal, loserAbort.signal]);
  return new Promise((resolve, reject) => {
    const errors: unknown[] = [];
    let launched = 0;
    let inFlight = 0;
    let settled = false;
    const settle = (finish: () => void) => { settled = true; clearTimeout(hedge); loserAbort.abort(); finish(); };
    const launch = () => {
      if (settled || launched >= providers.length || signal.aborted) return;
      const provider = providers[launched];
      launched += 1;
      inFlight += 1;
      onStage(launched === 1 ? "drafting" : "hedging");
      verifiedAttempt(provider, input, attemptSignal, onStage).then(
        copy => { if (!settled) settle(() => resolve(copy)); },
        error => {
          inFlight -= 1;
          errors.push(error);
          if (settled) return;
          launch();
          if (inFlight === 0) settle(() => reject(errors[0]));
        },
      );
    };
    const hedge = setTimeout(launch, HEDGE_DELAY_MS);
    launch();
  });
}

export async function rewriteInVoice(input: NewsfeedVoiceInput, options: RewriteOptions): Promise<NewsfeedVoiceCopy> {
  const key = cacheKey(input);
  const cached = cachedDraft(key);
  if (cached) return cached;
  options.signal.throwIfAborted();
  const copy = await raceProviders(rewriteProviders(), input, options);
  rememberDraft(key, copy);
  return copy;
}
