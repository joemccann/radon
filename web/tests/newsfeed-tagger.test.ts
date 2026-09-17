import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.restoreAllMocks();
});

const TAXONOMY = ["BTC", "VOL", "POSITIONING", "MACRO"];

describe("createTagger.tagPost (open vocabulary)", () => {
  it("returns the model's 3 tags normalised to uppercase", async () => {
    const completeJson = vi.fn().mockResolvedValue({ tags: ["puts", "put-call-ratio", "positioning"] });
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({
      getTaxonomySnapshot: async () => TAXONOMY,
      completeJson,
    });

    const tags = await tagger.tagPost({ id: "p1", title: "Hated puts", content: "Put call ratio imploded" });

    expect(tags).toEqual(["PUTS", "PUT-CALL-RATIO", "POSITIONING"]);
    expect(completeJson).toHaveBeenCalledTimes(1);
    const arg = completeJson.mock.calls[0][0];
    expect(arg.system).toMatch(/BTC, VOL, POSITIONING, MACRO/);
    expect(arg.system).toMatch(/EXACTLY 3 tags/);
    expect(arg.system).toMatch(/ALL TAGS ARE UPPERCASE/);
    expect(arg.instruction).toMatch(/Hated puts/);
  });

  it("primes the model with technical-analysis vocabulary (candlesticks, indicators, chart patterns)", async () => {
    const completeJson = vi.fn().mockResolvedValue({ tags: ["SHOOTING-STAR", "SPX", "EQUITIES"] });
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY, completeJson });

    const tags = await tagger.tagPost({
      id: "p-ta",
      title: "Shooting star",
      content: "SPX printed a large shooting star candle today, one of the more important signals to watch after a strong move.",
    });

    expect(tags).toEqual(["SHOOTING-STAR", "SPX", "EQUITIES"]);
    const systemMsg = completeJson.mock.calls[0][0].system as string;
    expect(systemMsg).toMatch(/TECHNICAL SIGNAL/);
    expect(systemMsg).toMatch(/SHOOTING-STAR/);
    expect(systemMsg).toMatch(/HAMMER/);
    expect(systemMsg).toMatch(/RSI/);
    expect(systemMsg).toMatch(/MACD/);
    expect(systemMsg).toMatch(/HEAD-SHOULDERS/);
    expect(systemMsg).toMatch(/SUPPORT/);
    expect(systemMsg).toMatch(/RESISTANCE/);
  });

  it("returns null when the ladder soft-fails", async () => {
    const completeJson = vi.fn().mockResolvedValue(null);
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY, completeJson });

    const tags = await tagger.tagPost({ id: "p3", title: "X", content: "Y" });
    expect(tags).toBeNull();
  });

  it("trims to exactly 3 tags when the model returns more", async () => {
    const completeJson = vi.fn().mockResolvedValue({
      tags: ["puts", "options", "positioning", "vol", "hedging"],
    });
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY, completeJson });

    const tags = await tagger.tagPost({ id: "p4", title: "X", content: "Y" });
    expect(tags).toEqual(["PUTS", "OPTIONS", "POSITIONING"]);
  });

  it("returns null if normalised tag count drops below 3", async () => {
    const completeJson = vi.fn().mockResolvedValue({ tags: ["puts", "", "  "] });
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY, completeJson });

    const tags = await tagger.tagPost({ id: "p5", title: "X", content: "Y" });
    expect(tags).toBeNull();
  });

  it("does not require CEREBRAS_API_KEY", async () => {
    const original = process.env.CEREBRAS_API_KEY;
    delete process.env.CEREBRAS_API_KEY;
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    expect(() => createTagger({
      getTaxonomySnapshot: async () => TAXONOMY,
      completeJson: async () => ({ tags: ["A", "B", "C"] }),
    })).not.toThrow();
    process.env.CEREBRAS_API_KEY = original;
  });

  it("throws if getTaxonomySnapshot is missing", async () => {
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    expect(() => createTagger({})).toThrow(/getTaxonomySnapshot/);
  });
});

describe("completeViaLadder (Python CLI bridge)", () => {
  it("spawns model_ladder_cli.py with accept=tags and never calls cerebras.ai", async () => {
    let stdin = "";
    const spawnImpl = vi.fn((_bin: string, args: string[]) => {
      const listeners: Record<string, Array<(value?: unknown) => void>> = {
        data: [],
        close: [],
        error: [],
      };
      return {
        stdin: {
          write(chunk: string) { stdin += chunk; },
          end() {
            queueMicrotask(() => {
              for (const fn of listeners.data) {
                fn(JSON.stringify({
                  ok: true,
                  data: { tags: ["PUTS", "OPTIONS", "POSITIONING"] },
                  provider: "anthropic",
                }));
              }
              for (const fn of listeners.close) fn(0);
            });
          },
        },
        stdout: { on(ev: string, fn: (value?: unknown) => void) { if (ev === "data") listeners.data.push(fn); } },
        stderr: { on() {} },
        on(ev: string, fn: (value?: unknown) => void) {
          if (ev === "close" || ev === "error") listeners[ev].push(fn);
        },
        kill() {},
        args,
      };
    });

    const { completeViaLadder } = await import("../../scripts/newsfeed/tagger.js");
    const data = await completeViaLadder({
      system: "Pick EXACTLY 3 tags",
      instruction: "Title: X\nBody: Y",
      spawnImpl: spawnImpl as never,
      pythonBin: "python3.13",
    });

    expect(data).toEqual({ tags: ["PUTS", "OPTIONS", "POSITIONING"] });
    expect(spawnImpl).toHaveBeenCalledTimes(1);
    const [, args] = spawnImpl.mock.calls[0];
    expect(args[0]).toMatch(/model_ladder_cli\.py$/);
    const payload = JSON.parse(stdin);
    expect(payload.accept).toBe("tags");
    expect(payload.system).toMatch(/EXACTLY 3 tags/);
    expect(JSON.stringify(payload)).not.toMatch(/cerebras\.ai/);
    expect(JSON.stringify(payload)).not.toMatch(/gpt-oss-120b/);
  });
});

describe("__normaliseTags (uppercase + kebab-case)", () => {
  it("uppercases everything", async () => {
    const { __normaliseTags } = await import("../../scripts/newsfeed/tagger.js");
    expect(__normaliseTags(["btc", "Vix", "USD", "puts", "Options"])).toEqual([
      "BTC",
      "VIX",
      "USD",
      "PUTS",
      "OPTIONS",
    ]);
  });

  it("uppercase-kebab-cases multi-word concepts", async () => {
    const { __normaliseTags } = await import("../../scripts/newsfeed/tagger.js");
    expect(
      __normaliseTags(["Put Call Ratio", "single stock vol", "FUND_FLOWS", "Tail Hedge"]),
    ).toEqual(["PUT-CALL-RATIO", "SINGLE-STOCK-VOL", "FUND-FLOWS", "TAIL-HEDGE"]);
  });

  it("preserves & in tickers like M&A", async () => {
    const { __normaliseTags } = await import("../../scripts/newsfeed/tagger.js");
    expect(__normaliseTags(["m&a", "S&P", "M&A"])).toEqual(["M&A", "S&P"]);
  });

  it("strips surrounding punctuation and quotes", async () => {
    const { __normaliseTags } = await import("../../scripts/newsfeed/tagger.js");
    expect(__normaliseTags(['"puts"', "#options", "calls.", "(positioning)"])).toEqual([
      "PUTS",
      "OPTIONS",
      "CALLS",
      "POSITIONING",
    ]);
  });

  it("dedupes after normalisation", async () => {
    const { __normaliseTags } = await import("../../scripts/newsfeed/tagger.js");
    expect(__normaliseTags(["BTC", "btc", "BTC.", "Bitcoin"])).toEqual(["BTC", "BITCOIN"]);
  });

  it("drops empty/whitespace tags", async () => {
    const { __normaliseTags } = await import("../../scripts/newsfeed/tagger.js");
    expect(__normaliseTags(["puts", "  ", "", null as unknown as string, "options"])).toEqual([
      "PUTS",
      "OPTIONS",
    ]);
  });
});

describe("hydrateTags", () => {
  it("skips posts that already have ≥3 tags by default", async () => {
    const { hydrateTags } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = { tagPost: vi.fn() };

    const posts = [
      { id: "p1", title: "x", content: "x", tags: ["BTC", "crypto", "vol"] },
      { id: "p2", title: "y", content: "y" },
    ];

    tagger.tagPost.mockResolvedValueOnce(["macro", "rates", "Fed"]);

    const updated = await hydrateTags(posts, tagger);

    expect(updated).toBe(true);
    expect(tagger.tagPost).toHaveBeenCalledTimes(1);
    expect(posts[0].tags).toEqual(["BTC", "crypto", "vol"]);
    expect(posts[1].tags).toEqual(["macro", "rates", "Fed"]);
  });

  it("re-tags every post when force is true", async () => {
    const { hydrateTags } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = { tagPost: vi.fn().mockResolvedValue(["macro", "rates", "Fed"]) };

    const posts = [
      { id: "p1", title: "x", content: "x", tags: ["BTC", "crypto", "vol"] },
      { id: "p2", title: "y", content: "y" },
    ];

    const updated = await hydrateTags(posts, tagger, { force: true });

    expect(updated).toBe(true);
    expect(tagger.tagPost).toHaveBeenCalledTimes(2);
    expect(posts[0].tags).toEqual(["macro", "rates", "Fed"]);
    expect(posts[1].tags).toEqual(["macro", "rates", "Fed"]);
  });

  it("invokes onNewTags for every successful tagPost result", async () => {
    const { hydrateTags } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = {
      tagPost: vi
        .fn()
        .mockResolvedValueOnce(["PUTS", "OPTIONS", "POSITIONING"])
        .mockResolvedValueOnce(["MACRO", "FED", "RATES"]),
    };
    const onNewTags = vi.fn().mockResolvedValue(undefined);
    const posts = [
      { id: "p1", title: "x", content: "x" },
      { id: "p2", title: "y", content: "y" },
    ];

    await hydrateTags(posts, tagger, { onNewTags });

    expect(onNewTags).toHaveBeenCalledTimes(2);
    expect(onNewTags).toHaveBeenNthCalledWith(1, ["PUTS", "OPTIONS", "POSITIONING"]);
    expect(onNewTags).toHaveBeenNthCalledWith(2, ["MACRO", "FED", "RATES"]);
  });

  it("leaves posts unchanged when tagger returns null", async () => {
    const { hydrateTags } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = { tagPost: vi.fn().mockResolvedValue(null) };

    const posts = [
      { id: "p1", title: "x", content: "x" },
      { id: "p2", title: "y", content: "y", tags: ["BTC"] },
    ];

    const updated = await hydrateTags(posts, tagger);

    expect(updated).toBe(false);
    expect(posts[0].tags).toBeUndefined();
    expect(posts[1].tags).toEqual(["BTC"]);
  });
});

// R-466 / REL-165: a hung ladder walk is a per-post tagging failure, never a cycle hang.
describe("R-466 / REL-165: the text ladder walk is bounded", () => {
  it("a completeJson that never answers settles tagPost as untagged within the bound", async () => {
    const completeJson = vi.fn(() => new Promise(() => {}));
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({
      getTaxonomySnapshot: async () => TAXONOMY,
      completeJson,
      timeoutMs: 50,
    });

    const outcome = await Promise.race([
      tagger.tagPost({ id: "p1", title: "Hangs", content: "x" }).then((tags) => ({ tags })),
      new Promise<string>((resolve) => setTimeout(() => resolve("hung"), 1500)),
    ]);

    expect(outcome).toEqual({ tags: null });
  });
});

describe("T-374: the default ladder bound is exactly 30 000 ms", () => {
  it("with no timeoutMs override, completeJson receives 30000", async () => {
    const completeJson = vi.fn().mockResolvedValue({ tags: ["puts", "options", "positioning"] });
    const { createTagger, DEFAULT_TAGGER_TIMEOUT_MS } = await import("../../scripts/newsfeed/tagger.js");
    expect(DEFAULT_TAGGER_TIMEOUT_MS).toBe(30_000);
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY, completeJson });

    const tags = await tagger.tagPost({ id: "p-t374", title: "X", content: "Y" });
    expect(tags).toEqual(["PUTS", "OPTIONS", "POSITIONING"]);
    expect(completeJson).toHaveBeenCalledWith(expect.objectContaining({ timeoutMs: 30_000 }));
  });
});
