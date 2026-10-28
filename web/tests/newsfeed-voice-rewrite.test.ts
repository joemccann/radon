import { HUMANIZER_VERSION } from "@/lib/humanizerPolicy";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const { editorialReview } = vi.hoisted(() => ({ editorialReview: vi.fn() }));
vi.mock("@/lib/humanizer", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/humanizer")>(), assertHumanizedCopy: editorialReview }));

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
  editorialReview.mockReset().mockResolvedValue({ score: 100, findings: [], qualitative: [] });
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
      humanizerVersion: HUMANIZER_VERSION,
      title: "Seasonality",
      content: "104 to 130. That is the historical path.",
      caption: "Seasonality\n\nMedian rises from 104 to 130.",
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
  it("falls back when editorial review rejects and caches only the reviewed result", async () => {
    chat.mockResolvedValue(draft("104 to 130."));
    editorialReview.mockRejectedValueOnce(new Error("Reviewer unavailable"));
    await expect(run().promise).resolves.toMatchObject({ content: "104 to 130." });
    expect(editorialReview).toHaveBeenCalledTimes(2);
    expect(chat).toHaveBeenCalledTimes(2);
    await run().promise;
    expect(editorialReview).toHaveBeenCalledTimes(2);
  });
  it("never returns or caches an unreviewed draft when reviewers fail", async () => {
    chat.mockResolvedValue(draft("104 to 130."));
    editorialReview.mockRejectedValue(new Error("Humanizer review unavailable"));
    await expect(run().promise).rejects.toThrow(/Humanizer/);
    await expect(run().promise).rejects.toThrow(/Humanizer/);
    expect(chat).toHaveBeenCalledTimes(4);
  });

  it("preserves a complete clean X caption while reviewing both caption and media", async () => {
    const full = { title: "Dollar levels", content: "DXY tests 102.86. Pictet says 104.59 is resistance. USD/JPY resistance is 158-159." };
    chat.mockResolvedValue(draft("DXY tests 102.86.", "Dollar levels"));
    const result = await run(full).promise;
    expect(result.caption).toBe("Dollar levels\n\nDXY tests 102.86.\n\nPictet says 104.59 is resistance.\n\nUSD/JPY resistance is 158-159.");
    expect(editorialReview).toHaveBeenCalledWith(expect.objectContaining({ caption: result.caption, mediaCaption: "Dollar levels\n\nDXY tests 102.86." }), expect.any(Object));
  });
  it("repairs the full caption and verifies facts before its final editorial review", async () => {
    const { HumanizerReviewError } = await import("@/lib/humanizer");
    const full = { title: "Dollar levels", content: "DXY tests 102.86. That's a conditional setup, not a confirmed breakout." };
    chat.mockResolvedValueOnce(draft("DXY tests 102.86.", "Dollar levels"))
      .mockResolvedValueOnce(draft("DXY tests 102.86. The breakout remains conditional.", "Dollar levels"))
      .mockResolvedValueOnce({ text: '{"supported":true}' });
    editorialReview.mockRejectedValueOnce(new HumanizerReviewError({ score: 100, findings: [], qualitative: [{field:"caption", text:"not a confirmed breakout", category:"negative_parallelism", start:0, end:24, deduction:0}] }));
    const result = await run(full).promise;
    expect(result.caption).toContain("The breakout remains conditional.");
    expect(chat).toHaveBeenCalledTimes(3);
    expect(editorialReview).toHaveBeenCalledTimes(2);
    expect(editorialReview.mock.calls[1][1].previousAudit.qualitative).toHaveLength(1);
  });

});
