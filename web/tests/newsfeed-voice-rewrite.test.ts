import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const { chat, resolveProvider, alternateProvider } = vi.hoisted(() => ({
  chat: vi.fn(),
  resolveProvider: vi.fn(),
  alternateProvider: vi.fn(),
}));
vi.mock("../lib/llm/provider", () => ({ chat, resolveProvider, alternateProvider }));
import {
  HEDGE_DELAY_MS,
  MODEL_SOURCE_CHARS,
  resetVoiceRewriteCache,
  rewriteInVoice,
} from "../lib/newsfeedVoiceRewrite";

const input = { title: "Seasonality", content: "Median rises from 104 to 130." };
const draft = (content: string, title = "Seasonality") => ({ text: JSON.stringify({ title, content }) });
const pending = () => new Promise<never>(() => {});

function run(value = input, signal = new AbortController().signal) {
  const stages: string[] = [];
  const promise = rewriteInVoice(value, { signal, onStage: stage => stages.push(stage) });
  return { promise, stages };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetVoiceRewriteCache();
  resolveProvider.mockReturnValue("xai");
  alternateProvider.mockReturnValue("anthropic");
});
afterEach(() => { vi.useRealTimers(); });

describe("rewriteInVoice", () => {
  it("returns the first verified draft from the primary without starting the alternate", async () => {
    chat.mockResolvedValue(draft("104 to 130. That is the historical path."));
    const { promise, stages } = run();
    await expect(promise).resolves.toEqual({
      title: "Seasonality",
      content: "104 to 130. That is the historical path.",
      caption: "Seasonality\n\n104 to 130.\n\nThat is the historical path.",
    });
    expect(chat).toHaveBeenCalledOnce();
    expect(chat.mock.calls[0][0]).toMatchObject({ provider: "xai", fallback: false, reasoningEffort: "low" });
    expect(stages).toEqual(["drafting", "checking"]);
  });

  it("sends the model only the lead of a long article", async () => {
    chat.mockResolvedValue(draft("104 to 130."));
    const long = { title: "Seasonality", content: `Median rises from 104 to 130. ${"x".repeat(MODEL_SOURCE_CHARS * 2)}` };
    await run(long).promise;
    const sent = JSON.parse(chat.mock.calls[0][0].messages[0].content);
    expect(sent.content).toHaveLength(MODEL_SOURCE_CHARS);
    expect(sent.title).toBe("Seasonality");
  });

  it("starts the alternate immediately when the primary fails", async () => {
    chat.mockRejectedValueOnce(new Error("xai 403")).mockResolvedValueOnce(draft("104 to 130."));
    const { promise, stages } = run();
    await expect(promise).resolves.toMatchObject({ content: "104 to 130." });
    expect(chat.mock.calls.map(call => call[0].provider)).toEqual(["xai", "anthropic"]);
    expect(stages).toEqual(["drafting", "hedging", "checking"]);
  });

  it("hedges a slow primary after the hedge delay and cancels the loser", async () => {
    vi.useFakeTimers();
    let primarySignal: AbortSignal | undefined;
    chat.mockImplementationOnce(({ signal }) => { primarySignal = signal; return pending(); })
      .mockResolvedValueOnce(draft("104 to 130."));
    const { promise } = run();
    await vi.advanceTimersByTimeAsync(HEDGE_DELAY_MS - 1);
    expect(chat).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    await expect(promise).resolves.toMatchObject({ content: "104 to 130." });
    expect(chat).toHaveBeenCalledTimes(2);
    expect(primarySignal?.aborted).toBe(true);
  });

  it("treats an invented number as a failed attempt and tries the alternate", async () => {
    chat.mockResolvedValueOnce(draft("Up 25%.")).mockResolvedValueOnce(draft("104 to 130."));
    await expect(run().promise).resolves.toMatchObject({ content: "104 to 130." });
    expect(chat).toHaveBeenCalledTimes(2);
  });

  it("accepts a reformatted numeric range but still rejects invented values", async () => {
    alternateProvider.mockReturnValue(undefined);
    const range = { title: "Wolfe sees $30-$50B for Meta", content: "Revenue opportunity from Muse." };
    chat.mockResolvedValueOnce(draft("Revenue opportunity from Muse", "Meta: $30B-$50B from Muse"));
    await expect(run(range).promise).resolves.toMatchObject({ title: "Meta: $30B-$50B from Muse" });
    resetVoiceRewriteCache();
    chat.mockResolvedValueOnce(draft("Revenue opportunity from Muse", "Meta: $30B-$60B from Muse"));
    await expect(run(range).promise).rejects.toThrow("Unsupported numerical claim");
  });

  it("rejects when every provider fails", async () => {
    chat.mockRejectedValueOnce(new Error("first")).mockRejectedValueOnce(new Error("second"));
    await expect(run().promise).rejects.toThrow("first");
    expect(chat).toHaveBeenCalledTimes(2);
  });

  it("does not launch the alternate once the caller has aborted", async () => {
    const controller = new AbortController();
    chat.mockImplementationOnce(({ signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(signal.reason));
    }));
    const { promise } = run(input, controller.signal);
    controller.abort();
    await expect(promise).rejects.toBeDefined();
    expect(chat).toHaveBeenCalledOnce();
  });

  it("serves a repeated rewrite from the cache without a model call", async () => {
    chat.mockResolvedValue(draft("104 to 130."));
    await run().promise;
    const { promise, stages } = run();
    await expect(promise).resolves.toMatchObject({ content: "104 to 130." });
    expect(chat).toHaveBeenCalledOnce();
    expect(stages).toEqual([]);
  });

  it("never caches a failed rewrite", async () => {
    alternateProvider.mockReturnValue(undefined);
    chat.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(draft("104 to 130."));
    await expect(run().promise).rejects.toThrow("boom");
    await expect(run().promise).resolves.toMatchObject({ content: "104 to 130." });
    expect(chat).toHaveBeenCalledTimes(2);
  });
});
