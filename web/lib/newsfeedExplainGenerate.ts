import { alternateProvider, chat, resolveProvider, type LlmContentBlock, type LlmProviderName } from "@/lib/llm/provider";
import {
  EXPLAIN_THIN_MESSAGE,
  NEWSFEED_EXPLAIN_SYSTEM,
  explainContentHash,
  isThinExplainInput,
  stripExplainDisclaimer,
  type NewsfeedExplainInput,
} from "@/lib/newsfeedExplain";
import { readExplainCache, writeExplainCache } from "@/lib/newsfeedExplainCache";
import { loadExplainImages } from "@/lib/newsfeedExplainImages";
import { EXPLAIN_BUDGET_MS, type ExplainStage } from "@/lib/newsfeedExplainProgress";

export type ExplainResult = {
  explanation: string;
  cached: boolean;
  thin: boolean;
  model: string;
  provider: string;
  hasImage: boolean;
};

type ExplainOptions = {
  signal: AbortSignal;
  onStage: (stage: ExplainStage) => void;
  token?: string;
};

type Provider = Exclude<LlmProviderName, "grok">;

const flights = new Map<string, Promise<ExplainResult>>();

export function resetExplainSingleFlight(): void {
  flights.clear();
}

function thinResult(): ExplainResult {
  return {
    explanation: EXPLAIN_THIN_MESSAGE,
    cached: false,
    thin: true,
    model: "",
    provider: "",
    hasImage: false,
  };
}

function pickProvider(hasImages: boolean): { provider: Provider; sendImages: boolean } {
  const primary = resolveProvider();
  if (!hasImages) return { provider: primary, sendImages: false };
  if (primary === "anthropic" || primary === "xai") return { provider: primary, sendImages: true };
  const alternate = alternateProvider(primary);
  if (alternate === "anthropic" || alternate === "xai") return { provider: alternate, sendImages: true };
  return { provider: primary, sendImages: false };
}

function beginFlight(key: string, work: () => Promise<ExplainResult>): Promise<ExplainResult> {
  const existing = flights.get(key);
  if (existing) return existing;
  const promise = work().finally(() => {
    if (flights.get(key) === promise) flights.delete(key);
  });
  flights.set(key, promise);
  return promise;
}

async function draftExplanation(
  input: NewsfeedExplainInput,
  contentHash: string,
  { signal, onStage, token }: ExplainOptions,
): Promise<ExplainResult> {
  onStage("loading");
  const loaded = input.imageUrls.length > 0
    ? await loadExplainImages(input.imageUrls, { token, signal })
    : [];
  if (input.title.length + input.content.length < 40 && loaded.length === 0) {
    return thinResult();
  }
  const { provider, sendImages } = pickProvider(loaded.length > 0);
  const images = sendImages ? loaded : [];
  onStage("drafting");
  const result = await chat({
    provider,
    fallback: false,
    system: NEWSFEED_EXPLAIN_SYSTEM,
    messages: [{
      role: "user",
      content: [
        ...images,
        { type: "text", text: JSON.stringify({ title: input.title, content: input.content }) },
      ] as LlmContentBlock[],
    }],
    reasoningEffort: "low",
    maxTokens: 700,
    timeoutMs: EXPLAIN_BUDGET_MS,
    signal,
  });
  onStage("checking");
  const explanation = stripExplainDisclaimer(result.text);
  if (!explanation) throw new Error("Empty explanation");
  const hasImage = images.length > 0;
  await writeExplainCache({
    postId: input.postId,
    contentHash,
    explanation,
    model: result.model,
    provider: result.provider,
    hasImage,
  });
  return {
    explanation,
    cached: false,
    thin: false,
    model: result.model,
    provider: result.provider,
    hasImage,
  };
}

export async function explainNewsfeedPost(
  input: NewsfeedExplainInput,
  options: ExplainOptions,
): Promise<ExplainResult> {
  const contentHash = explainContentHash(input);
  const cached = await readExplainCache(input.postId, contentHash);
  if (cached) {
    options.onStage("loading");
    return { ...cached, cached: true, thin: false };
  }
  if (isThinExplainInput(input)) return thinResult();
  options.signal.throwIfAborted();
  return beginFlight(`${input.postId}:${contentHash}`, () => draftExplanation(input, contentHash, options));
}
