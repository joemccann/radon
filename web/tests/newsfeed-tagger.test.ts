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

const TAXONOMY = ["BTC", "ETH", "crypto", "vol", "VIX", "macro", "rates", "Fed"];

describe("createTagger.tagPost", () => {
  it("returns ≥3 tags from the taxonomy on a successful gpt-oss-120b response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(chatCompletion(JSON.stringify({ tags: ["BTC", "crypto", "vol"] }))),
    );
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ taxonomy: TAXONOMY });

    const tags = await tagger.tagPost({ id: "p1", title: "BTC rally", content: "Spot-led" });

    expect(tags).toEqual(["BTC", "crypto", "vol"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.model).toBe("gpt-oss-120b");
    expect(init.headers.Authorization).toBe("Bearer test-key");
  });

  it("falls back to qwen-3-235b on a 429 from gpt-oss-120b", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: "rate limited" }, 429))
      .mockResolvedValueOnce(
        jsonResponse(chatCompletion(JSON.stringify({ tags: ["macro", "rates", "Fed"] }))),
      );
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ taxonomy: TAXONOMY });

    const tags = await tagger.tagPost({ id: "p2", title: "Fed cuts", content: "Rate path" });

    expect(tags).toEqual(["macro", "rates", "Fed"]);
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
    const tagger = createTagger({ taxonomy: TAXONOMY });

    const tags = await tagger.tagPost({ id: "p3", title: "X", content: "Y" });

    expect(tags).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("filters tags to taxonomy intersection and rejects responses with <3 valid tags", async () => {
    const fetchMock = vi.fn()
      // gpt-oss returns 2 valid + 1 invalid → reject, fall back
      .mockResolvedValueOnce(
        jsonResponse(chatCompletion(JSON.stringify({ tags: ["BTC", "crypto", "GARBAGE"] }))),
      )
      // llama returns 3 valid
      .mockResolvedValueOnce(
        jsonResponse(chatCompletion(JSON.stringify({ tags: ["BTC", "ETH", "crypto", "vol"] }))),
      );
    global.fetch = fetchMock as typeof fetch;

    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = createTagger({ taxonomy: TAXONOMY });

    const tags = await tagger.tagPost({ id: "p4", title: "X", content: "Y" });

    // First response had only 2 valid (BTC, crypto) — discarded.
    // Second response has 4 valid; keeps all 4.
    expect(tags).toEqual(["BTC", "ETH", "crypto", "vol"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("throws if CEREBRAS_API_KEY is not set", async () => {
    delete process.env.CEREBRAS_API_KEY;
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    expect(() => createTagger({ taxonomy: TAXONOMY })).toThrow(/CEREBRAS_API_KEY/);
  });

  it("throws if taxonomy is empty", async () => {
    const { createTagger } = await import("../../scripts/newsfeed/tagger.js");
    expect(() => createTagger({ taxonomy: [] })).toThrow(/taxonomy/);
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
    expect(tagger.tagPost).toHaveBeenCalledWith(expect.objectContaining({ id: "p2" }));
    expect(posts[0].tags).toEqual(["BTC", "crypto", "vol"]); // untouched
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

  it("leaves untagged posts unchanged when tagger returns null (don't blank existing tags)", async () => {
    const { hydrateTags } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = { tagPost: vi.fn().mockResolvedValue(null) };

    const posts = [
      { id: "p1", title: "x", content: "x" }, // no tags yet
      { id: "p2", title: "y", content: "y", tags: ["BTC"] }, // partial
    ];

    const updated = await hydrateTags(posts, tagger);

    expect(updated).toBe(false);
    expect(posts[0].tags).toBeUndefined();
    expect(posts[1].tags).toEqual(["BTC"]);
  });

  it("respects throttleMs between calls", async () => {
    const { hydrateTags } = await import("../../scripts/newsfeed/tagger.js");
    const tagger = { tagPost: vi.fn().mockResolvedValue(["macro", "rates", "Fed"]) };

    const posts = [
      { id: "p1", title: "x", content: "x" },
      { id: "p2", title: "y", content: "y" },
      { id: "p3", title: "z", content: "z" },
    ];

    const start = Date.now();
    await hydrateTags(posts, tagger, { throttleMs: 50 });
    const elapsed = Date.now() - start;

    // 3 calls, throttle between calls 1→2 and 2→3 → expect ≥100ms total
    expect(elapsed).toBeGreaterThanOrEqual(95);
  });
});
