import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const originalFetch = global.fetch;
const originalKey = process.env.CEREBRAS_API_KEY;

beforeEach(() => {
  process.env.CEREBRAS_API_KEY = "test-key";
});

afterEach(() => {
  global.fetch = originalFetch;
  process.env.CEREBRAS_API_KEY = originalKey;
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function chatCompletion(content: string): unknown {
  return { choices: [{ message: { content } }] };
}

const TAXONOMY = ["BTC", "VOL", "POSITIONING", "MACRO"];

describe("createTagger.tagPost (open vocabulary)", () => {
  it("returns the model's 3 tags normalised to uppercase", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(chatCompletion(JSON.stringify({ tags: ["puts", "put-call-ratio", "positioning"] }))),
    );
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({
      getTaxonomySnapshot: async () => TAXONOMY,
    });

    const tags = await tagger.tagPost({ id: "p1", title: "Hated puts", content: "Put call ratio imploded" });

    expect(tags).toEqual(["PUTS", "PUT-CALL-RATIO", "POSITIONING"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.model).toBe("gpt-oss-120b");
    const systemMsg = body.messages.find((m: { role: string }) => m.role === "system").content;
    expect(systemMsg).toMatch(/BTC, VOL, POSITIONING, MACRO/);
    expect(systemMsg).toMatch(/EXACTLY 3 tags/);
    expect(systemMsg).toMatch(/ALL TAGS ARE UPPERCASE/);
  });

  it("primes the model with technical-analysis vocabulary (candlesticks, indicators, chart patterns)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(chatCompletion(JSON.stringify({ tags: ["SHOOTING-STAR", "SPX", "EQUITIES"] }))),
    );
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY });

    const tags = await tagger.tagPost({
      id: "p-ta",
      title: "Shooting star",
      content: "SPX printed a large shooting star candle today, one of the more important signals to watch after a strong move.",
    });

    expect(tags).toEqual(["SHOOTING-STAR", "SPX", "EQUITIES"]);

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    const systemMsg = body.messages.find((m: { role: string }) => m.role === "system").content;
    expect(systemMsg).toMatch(/TECHNICAL SIGNAL/);
    expect(systemMsg).toMatch(/SHOOTING-STAR/);
    expect(systemMsg).toMatch(/HAMMER/);
    expect(systemMsg).toMatch(/RSI/);
    expect(systemMsg).toMatch(/MACD/);
    expect(systemMsg).toMatch(/HEAD-SHOULDERS/);
    expect(systemMsg).toMatch(/SUPPORT/);
    expect(systemMsg).toMatch(/RESISTANCE/);
  });

  it("falls back to qwen-3-235b on a 429 from gpt-oss-120b", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: "rate limited" }, 429))
      .mockResolvedValueOnce(
        jsonResponse(chatCompletion(JSON.stringify({ tags: ["MACRO", "FED", "RATES"] }))),
      );
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY });

    const tags = await tagger.tagPost({ id: "p2", title: "Fed cuts", content: "Rate path" });

    expect(tags).toEqual(["MACRO", "FED", "RATES"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const second = JSON.parse(fetchMock.mock.calls[1][1].body as string);
    expect(second.model).toBe("qwen-3-235b-a22b-instruct-2507");
  });

  it("returns null when both gpt-oss and qwen fail", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: "rate limited" }, 429))
      .mockResolvedValueOnce(jsonResponse({ error: "rate limited" }, 429));
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY });

    const tags = await tagger.tagPost({ id: "p3", title: "X", content: "Y" });

    expect(tags).toBeNull();
  });

  it("trims to exactly 3 tags when the model returns more", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(chatCompletion(JSON.stringify({ tags: ["puts", "options", "positioning", "vol", "hedging"] }))),
    );
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY });

    const tags = await tagger.tagPost({ id: "p4", title: "X", content: "Y" });
    expect(tags).toEqual(["PUTS", "OPTIONS", "POSITIONING"]);
  });

  it("falls back if normalised tag count drops below 3 (post-cleanup junk)", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        jsonResponse(chatCompletion(JSON.stringify({ tags: ["puts", "", "  "] }))),
      )
      .mockResolvedValueOnce(
        jsonResponse(chatCompletion(JSON.stringify({ tags: ["puts", "options", "positioning"] }))),
      );
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ getTaxonomySnapshot: async () => TAXONOMY });

    const tags = await tagger.tagPost({ id: "p5", title: "X", content: "Y" });
    expect(tags).toEqual(["PUTS", "OPTIONS", "POSITIONING"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("throws if CEREBRAS_API_KEY is not set", async () => {
    delete process.env.CEREBRAS_API_KEY;
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    expect(() => createTagger({ getTaxonomySnapshot: async () => TAXONOMY })).toThrow(/CEREBRAS_API_KEY/);
  });

  it("throws if getTaxonomySnapshot is missing", async () => {
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    expect(() => createTagger({})).toThrow(/getTaxonomySnapshot/);
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
