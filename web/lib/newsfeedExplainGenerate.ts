import { assertHumanizedCopy, assertLocalHumanizer } from "./humanizer";
import { chat, type LlmContentBlock } from "@/lib/llm/provider";
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

function callerAbortError(signal: AbortSignal): DOMException {
  const reason = signal.reason;
  if (reason instanceof DOMException) return reason;
  return new DOMException("The operation was aborted", "AbortError");
}

/** A reader who leaves rejects only their wait. The draft keeps its own deadline (REL-327). */
function waitForCaller(flight: Promise<ExplainResult>, signal: AbortSignal): Promise<ExplainResult> {
  if (signal.aborted) return Promise.reject(callerAbortError(signal));
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(callerAbortError(signal));
    signal.addEventListener("abort", onAbort, { once: true });
    flight.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

function beginFlight(
  key: string,
  work: (signal: AbortSignal) => Promise<ExplainResult>,
): Promise<ExplainResult> {
  const existing = flights.get(key);
  if (existing) return existing;
  const signal = AbortSignal.timeout(EXPLAIN_BUDGET_MS);
  const promise = work(signal).finally(() => {
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
  const images = loaded;
  onStage("drafting");
  const result = await chat({
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
  await assertHumanizedCopy({ content: explanation }, { signal, timeoutMs: EXPLAIN_BUDGET_MS });
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
  let cachePassed = false;
  if (cached) {
    try { assertLocalHumanizer({ content: cached.explanation }); cachePassed = true; } catch { /* Regenerate rejected cache entries. */ }
  }
  if (cached && cachePassed) {
    options.onStage("loading");
    return { ...cached, cached: true, thin: false };
  }
  if (isThinExplainInput(input)) return thinResult();
  options.signal.throwIfAborted();
  const flight = beginFlight(`${input.postId}:${contentHash}`, (signal) =>
    draftExplanation(input, contentHash, { ...options, signal }),
  );
  return waitForCaller(flight, options.signal);
}
