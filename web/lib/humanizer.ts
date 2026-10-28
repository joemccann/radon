import { chat, type LlmProviderName } from "@/lib/llm/provider";
import { HUMANIZER_REVIEW_SYSTEM } from "./humanizerPolicy";
import { assertLocalHumanizer, assertPassed, auditHumanizerCopy, type HumanizerAudit } from "./humanizerCheck";
export * from "./humanizerCheck";

/** Review the exact final strings; provider failure and malformed reviews fail closed. */
export async function assertHumanizedCopy(copy: Record<string, string>, options: { provider: LlmProviderName; signal: AbortSignal; timeoutMs?: number; previousAudit?: HumanizerAudit }): Promise<HumanizerAudit> {
  assertLocalHumanizer(copy);
  options.signal.throwIfAborted();
  const result = await chat({
    provider: options.provider,
    fallback: false,
    system: HUMANIZER_REVIEW_SYSTEM,
    messages: [{ role: "user", content: JSON.stringify(copy) }],
    maxTokens: 2400,
    reasoningEffort: "low",
    timeoutMs: options.timeoutMs ?? 90_000,
    signal: options.signal,
  });
  const raw = result.text.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  let review: unknown;
  try { review = JSON.parse(raw); } catch { throw new Error("Humanizer review is malformed"); }
  return assertPassed(auditHumanizerCopy(copy, review, options.previousAudit));
}
