import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const originalFetch = global.fetch;
const originalKey = process.env.ANTHROPIC_API_KEY;

beforeEach(() => {
  process.env.ANTHROPIC_API_KEY = "test-anthropic-key";
});

afterEach(() => {
  global.fetch = originalFetch;
  process.env.ANTHROPIC_API_KEY = originalKey;
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function anthropicCompletion(content: string): unknown {
  return {
    id: "msg_test",
    type: "message",
    role: "assistant",
    content: [{ type: "text", text: content }],
    model: "claude-haiku-4-5",
    usage: { input_tokens: 1000, output_tokens: 50 },
  };
}

const TAXONOMY = ["BTC", "VOL", "POSITIONING", "EQUITIES"];
const PUBLIC_ROOT = "/fake/public";

describe("createVisionTagger.tagPost", () => {
  it("sends image + caption to Anthropic and returns the model's 3 tags normalised", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(anthropicCompletion(JSON.stringify({ tags: ["shooting-star", "spx", "equities"] }))),
    );
    global.fetch = fetchMock as typeof fetch;

    const fakeImage = Buffer.from([0x89, 0x50, 0x4e, 0x47]);

    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({
      publicRoot: PUBLIC_ROOT,
      getTaxonomySnapshot: async () => TAXONOMY,
      readImage: async () => fakeImage,
    });

    const tags = await tagger.tagPost({
      id: "p1",
      title: "Shooting star",
      content: "SPX printed a shooting star candle",
      images: ["/media/p1-01.png"],
    });

    expect(tags).toEqual(["SHOOTING-STAR", "SPX", "EQUITIES"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.anthropic.com/v1/messages");
    expect((init as RequestInit).headers).toMatchObject({
      "x-api-key": "test-anthropic-key",
      "anthropic-version": "2023-06-01",
    });

    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.model).toBe("claude-haiku-4-5");
    expect(body.system).toMatch(/EXACTLY 3 tags/);
    expect(body.system).toMatch(/SHOOTING-STAR/);
    expect(body.system).toMatch(/RSI/);
    expect(body.system).toMatch(/BTC, VOL, POSITIONING, EQUITIES/);

    const userContent = body.messages[0].content;
    expect(userContent[0]).toMatchObject({
      type: "image",
      source: { type: "base64", media_type: "image/png" },
    });
    expect(typeof userContent[0].source.data).toBe("string");
    expect(userContent[0].source.data.length).toBeGreaterThan(0);
    expect(userContent[1].type).toBe("text");
    expect(userContent[1].text).toMatch(/An image is attached/);
    expect(userContent[1].text).toMatch(/Title: Shooting star/);
    expect(userContent[1].text).toMatch(/SPX printed a shooting star/);
  });

  it("returns null when post has no image (router falls back to text tagger)", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as typeof fetch;

    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({
      publicRoot: PUBLIC_ROOT,
      getTaxonomySnapshot: async () => TAXONOMY,
      readImage: async () => Buffer.from([]),
    });

    const tags = await tagger.tagPost({ id: "p2", title: "Text only", content: "No chart" });
    expect(tags).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns null when image file is missing", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as typeof fetch;

    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({
      publicRoot: PUBLIC_ROOT,
      getTaxonomySnapshot: async () => TAXONOMY,
      readImage: async () => {
        throw new Error("ENOENT");
      },
    });

    const tags = await tagger.tagPost({
      id: "p3",
      title: "x",
      content: "x",
      images: ["/media/missing.png"],
    });

    expect(tags).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns null when API errors and logs a warning", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error: "boom" }, 400));
    global.fetch = fetchMock as typeof fetch;
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({
      publicRoot: PUBLIC_ROOT,
      getTaxonomySnapshot: async () => TAXONOMY,
      readImage: async () => Buffer.from([1, 2, 3]),
    });

    const tags = await tagger.tagPost({
      id: "p4",
      title: "x",
      content: "x",
      images: ["/media/p4.png"],
    });

    expect(tags).toBeNull();
    expect(warn).toHaveBeenCalled();
  });

  it("trims to exactly 3 tags when the model returns more", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        anthropicCompletion(JSON.stringify({ tags: ["shooting-star", "spx", "equities", "candlestick", "reversal"] })),
      ),
    );
    global.fetch = fetchMock as typeof fetch;

    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({
      publicRoot: PUBLIC_ROOT,
      getTaxonomySnapshot: async () => TAXONOMY,
      readImage: async () => Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    });

    const tags = await tagger.tagPost({
      id: "p5",
      title: "x",
      content: "x",
      images: ["/media/p5.png"],
    });

    expect(tags).toEqual(["SHOOTING-STAR", "SPX", "EQUITIES"]);
  });

  it("tolerates Claude wrapping JSON in prose (extracts the {...} block)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        anthropicCompletion(
          'Sure, here are the tags:\n{"tags":["RSI","DIVERGENCE","SPX"]}\nLet me know if you need more.',
        ),
      ),
    );
    global.fetch = fetchMock as typeof fetch;

    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({
      publicRoot: PUBLIC_ROOT,
      getTaxonomySnapshot: async () => TAXONOMY,
      readImage: async () => Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    });

    const tags = await tagger.tagPost({
      id: "p6",
      title: "x",
      content: "x",
      images: ["/media/p6.png"],
    });
    expect(tags).toEqual(["RSI", "DIVERGENCE", "SPX"]);
  });

  it("recovers when Claude emits two JSON objects on separate lines (regression: position-55 bug)", async () => {
    // This was the live failure mode: a greedy regex glued both objects
    // together and JSON.parse choked at "line 2 column 1".
    const twoBlocks = '{"tags":["MARKET-BREADTH","DIVERGENCE","EQUITIES"]}\n{"tags":["MEAN-REVERSION","DIVERGENCE","SPX"]}';
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(anthropicCompletion(twoBlocks)));
    global.fetch = fetchMock as typeof fetch;

    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({
      publicRoot: PUBLIC_ROOT,
      getTaxonomySnapshot: async () => TAXONOMY,
      readImage: async () => Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    });

    const tags = await tagger.tagPost({ id: "p7", title: "x", content: "x", images: ["/media/p7.png"] });
    expect(tags).toEqual(["MARKET-BREADTH", "DIVERGENCE", "EQUITIES"]);
  });

  it("skips a leading non-tags object and picks the next one with a tags array", async () => {
    const text = '{"reasoning":"the chart shows divergence"}\n{"tags":["RSI","DIVERGENCE","SPX"]}';
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(anthropicCompletion(text)));
    global.fetch = fetchMock as typeof fetch;

    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({
      publicRoot: PUBLIC_ROOT,
      getTaxonomySnapshot: async () => TAXONOMY,
      readImage: async () => Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    });

    const tags = await tagger.tagPost({ id: "p8", title: "x", content: "x", images: ["/media/p8.png"] });
    expect(tags).toEqual(["RSI", "DIVERGENCE", "SPX"]);
  });

  it("throws when ANTHROPIC_API_KEY is unset", async () => {
    delete process.env.ANTHROPIC_API_KEY;
    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(() =>
      createVisionTagger({
        publicRoot: PUBLIC_ROOT,
        getTaxonomySnapshot: async () => TAXONOMY,
      }),
    ).toThrow(/ANTHROPIC_API_KEY/);
  });

  it("throws when publicRoot is missing", async () => {
    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(() =>
      createVisionTagger({
        getTaxonomySnapshot: async () => TAXONOMY,
      }),
    ).toThrow(/publicRoot/);
  });
});

describe("__extractFirstJsonObject (balanced-brace extractor)", () => {
  it("extracts a single flat object", async () => {
    const { __extractFirstJsonObject } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__extractFirstJsonObject('{"tags":["A","B","C"]}')).toBe('{"tags":["A","B","C"]}');
  });

  it("returns only the FIRST object when two are concatenated", async () => {
    const { __extractFirstJsonObject } = await import("../../scripts/newsfeed/vision_tagger.js");
    const text = '{"tags":["A","B","C"]}\n{"tags":["X","Y","Z"]}';
    expect(__extractFirstJsonObject(text)).toBe('{"tags":["A","B","C"]}');
  });

  it("ignores braces that appear inside string literals", async () => {
    const { __extractFirstJsonObject } = await import("../../scripts/newsfeed/vision_tagger.js");
    const text = '{"tags":["A","B with } brace","C"]}';
    expect(__extractFirstJsonObject(text)).toBe(text);
  });

  it("handles escaped quotes inside string literals", async () => {
    const { __extractFirstJsonObject } = await import("../../scripts/newsfeed/vision_tagger.js");
    const text = '{"note":"he said \\"hi\\"","tags":["A","B","C"]}';
    expect(__extractFirstJsonObject(text)).toBe(text);
  });

  it("handles nested objects (returns outermost balanced)", async () => {
    const { __extractFirstJsonObject } = await import("../../scripts/newsfeed/vision_tagger.js");
    const text = '{"meta":{"src":"x"},"tags":["A","B","C"]}';
    expect(__extractFirstJsonObject(text)).toBe(text);
  });

  it("returns null when no balanced object exists", async () => {
    const { __extractFirstJsonObject } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__extractFirstJsonObject("just prose with no JSON")).toBeNull();
    expect(__extractFirstJsonObject('{"unclosed":')).toBeNull();
  });

  it("skips leading prose before finding the object", async () => {
    const { __extractFirstJsonObject } = await import("../../scripts/newsfeed/vision_tagger.js");
    const text = 'Here is the answer:\n{"tags":["A","B","C"]}';
    expect(__extractFirstJsonObject(text)).toBe('{"tags":["A","B","C"]}');
  });
});

describe("__resolveImageAbsolutePath / __detectMediaType", () => {
  it("resolves leading-slash and bare relative paths against publicRoot", async () => {
    const { __resolveImageAbsolutePath } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__resolveImageAbsolutePath({ images: ["/media/x.png"] }, "/root")).toBe("/root/media/x.png");
    expect(__resolveImageAbsolutePath({ images: ["media/x.png"] }, "/root")).toBe("/root/media/x.png");
  });

  it("returns null for empty/missing images", async () => {
    const { __resolveImageAbsolutePath } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__resolveImageAbsolutePath({}, "/root")).toBeNull();
    expect(__resolveImageAbsolutePath({ images: [] }, "/root")).toBeNull();
    expect(__resolveImageAbsolutePath({ images: [""] }, "/root")).toBeNull();
  });

  it("detects media type from extension", async () => {
    const { __detectMediaType } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__detectMediaType("/x/y.png")).toBe("image/png");
    expect(__detectMediaType("/x/y.PNG")).toBe("image/png");
    expect(__detectMediaType("/x/y.jpg")).toBe("image/jpeg");
    expect(__detectMediaType("/x/y.jpeg")).toBe("image/jpeg");
    expect(__detectMediaType("/x/y.webp")).toBe("image/webp");
    expect(__detectMediaType("/x/y.gif")).toBe("image/gif");
    expect(__detectMediaType("/x/y.svg")).toBeNull();
  });
});

describe("__unionTags", () => {
  it("dedupes preserving order (text first, then vision)", async () => {
    const { __unionTags } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__unionTags(["A", "B", "C"], ["B", "D", "E"])).toEqual(["A", "B", "C", "D", "E"]);
  });

  it("ignores empty/non-string entries", async () => {
    const { __unionTags } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__unionTags(["A", "", null as unknown as string], ["B", undefined as unknown as string])).toEqual(["A", "B"]);
  });

  it("handles missing arms", async () => {
    const { __unionTags } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__unionTags(["A", "B"], null)).toEqual(["A", "B"]);
    expect(__unionTags(undefined, ["X", "Y"])).toEqual(["X", "Y"]);
    expect(__unionTags(undefined, undefined)).toEqual([]);
  });
});

describe("hydrateTagsDual", () => {
  it("runs BOTH classifiers per post and stamps tags_text, tags_vision, and union tags", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["EQUITIES", "MARKET-STRUCTURE", "POSITIONING"]) };
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(["SHOOTING-STAR", "INVERSE-HAMMER", "SPX"]) };

    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");
    const post = { id: "p1", title: "Shooting star", content: "x", images: ["/media/p1.png"] };

    const updated = await hydrateTagsDual([post], { textTagger, visionTagger });

    expect(updated).toBe(true);
    expect(textTagger.tagPost).toHaveBeenCalledTimes(1);
    expect(visionTagger.tagPost).toHaveBeenCalledTimes(1);
    expect((post as any).tags_text).toEqual(["EQUITIES", "MARKET-STRUCTURE", "POSITIONING"]);
    expect((post as any).tags_vision).toEqual(["SHOOTING-STAR", "INVERSE-HAMMER", "SPX"]);
    // SHOOTING-STAR / INVERSE-HAMMER are TA children, so TECHNICAL-ANALYSIS
    // is auto-appended to the union (parent enrichment).
    expect((post as any).tags).toEqual([
      "EQUITIES",
      "MARKET-STRUCTURE",
      "POSITIONING",
      "SHOOTING-STAR",
      "INVERSE-HAMMER",
      "SPX",
      "TECHNICAL-ANALYSIS",
    ]);
  });

  it("dedupes overlapping tags between text and vision in the union", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["EQUITIES", "MARKET-STRUCTURE", "POSITIONING"]) };
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(["SHOOTING-STAR", "EQUITIES", "SPX"]) };

    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");
    const post = { id: "p1", title: "x", content: "x", images: ["/media/p1.png"] };
    await hydrateTagsDual([post], { textTagger, visionTagger });

    // EQUITIES dedupes; SHOOTING-STAR triggers TECHNICAL-ANALYSIS enrichment.
    expect((post as any).tags).toEqual([
      "EQUITIES",
      "MARKET-STRUCTURE",
      "POSITIONING",
      "SHOOTING-STAR",
      "SPX",
      "TECHNICAL-ANALYSIS",
    ]);
  });

  it("skips a post when both classifications are already complete", async () => {
    const textTagger = { tagPost: vi.fn() };
    const visionTagger = { tagPost: vi.fn() };
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    const post = {
      id: "p1",
      title: "x",
      content: "x",
      images: ["/media/p1.png"],
      tags_text: ["A", "B", "C"],
      tags_vision: ["X", "Y", "Z"],
      tags: ["A", "B", "C", "X", "Y", "Z"],
    };

    const updated = await hydrateTagsDual([post], { textTagger, visionTagger });
    expect(updated).toBe(false);
    expect(textTagger.tagPost).not.toHaveBeenCalled();
    expect(visionTagger.tagPost).not.toHaveBeenCalled();
  });

  it("only runs the missing classifier when the other is already complete", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["MACRO", "FED", "RATES"]) };
    const visionTagger = { tagPost: vi.fn() };
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    const post = {
      id: "p1",
      title: "x",
      content: "x",
      images: ["/media/p1.png"],
      tags_vision: ["SHOOTING-STAR", "SPX", "EQUITIES"],
    };

    await hydrateTagsDual([post], { textTagger, visionTagger });

    expect(textTagger.tagPost).toHaveBeenCalledTimes(1);
    expect(visionTagger.tagPost).not.toHaveBeenCalled();
    expect((post as any).tags_text).toEqual(["MACRO", "FED", "RATES"]);
    expect((post as any).tags).toEqual([
      "MACRO",
      "FED",
      "RATES",
      "SHOOTING-STAR",
      "SPX",
      "EQUITIES",
      "TECHNICAL-ANALYSIS",
    ]);
  });

  it("does NOT run vision tagger for posts with no image", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["A", "B", "C"]) };
    const visionTagger = { tagPost: vi.fn() };
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    const post = { id: "p1", title: "x", content: "x" };
    await hydrateTagsDual([post], { textTagger, visionTagger });

    expect(textTagger.tagPost).toHaveBeenCalledTimes(1);
    expect(visionTagger.tagPost).not.toHaveBeenCalled();
    expect((post as any).tags_text).toEqual(["A", "B", "C"]);
    expect((post as any).tags_vision).toBeUndefined();
    expect((post as any).tags).toEqual(["A", "B", "C"]);
  });

  it("treats image-less posts as vision-complete in cursor logic", async () => {
    const textTagger = { tagPost: vi.fn() };
    const visionTagger = { tagPost: vi.fn() };
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    // No image, text already done — fully complete, nothing to do.
    const post = { id: "p1", title: "x", content: "x", tags_text: ["A", "B", "C"] };
    const updated = await hydrateTagsDual([post], { textTagger, visionTagger });

    expect(updated).toBe(false);
    expect(textTagger.tagPost).not.toHaveBeenCalled();
    expect(visionTagger.tagPost).not.toHaveBeenCalled();
  });

  it("force re-runs both classifiers regardless of existing state", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["NEW1", "NEW2", "NEW3"]) };
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(["VIS1", "VIS2", "VIS3"]) };
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    const post = {
      id: "p1",
      title: "x",
      content: "x",
      images: ["/media/p1.png"],
      tags_text: ["OLD1", "OLD2", "OLD3"],
      tags_vision: ["OLDV1", "OLDV2", "OLDV3"],
    };

    await hydrateTagsDual([post], { textTagger, visionTagger, force: true });

    expect(textTagger.tagPost).toHaveBeenCalledTimes(1);
    expect(visionTagger.tagPost).toHaveBeenCalledTimes(1);
    expect((post as any).tags_text).toEqual(["NEW1", "NEW2", "NEW3"]);
    expect((post as any).tags_vision).toEqual(["VIS1", "VIS2", "VIS3"]);
  });

  it("invokes onNewTags once per successful classifier per post", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["A", "B", "C"]) };
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(["X", "Y", "Z"]) };
    const onNewTags = vi.fn().mockResolvedValue(undefined);
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    const post = { id: "p1", title: "x", content: "x", images: ["/media/p1.png"] };
    await hydrateTagsDual([post], { textTagger, visionTagger, onNewTags });

    expect(onNewTags).toHaveBeenCalledTimes(2);
    expect(onNewTags).toHaveBeenNthCalledWith(1, ["A", "B", "C"]);
    expect(onNewTags).toHaveBeenNthCalledWith(2, ["X", "Y", "Z"]);
  });

  it("leaves a post unchanged when both classifiers return null", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(null) };
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(null) };
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    const post = { id: "p1", title: "x", content: "x", images: ["/media/p1.png"] };
    const updated = await hydrateTagsDual([post], { textTagger, visionTagger });

    expect(updated).toBe(false);
    expect((post as any).tags_text).toBeUndefined();
    expect((post as any).tags_vision).toBeUndefined();
    expect((post as any).tags).toBeUndefined();
  });

  it("partial success: only one classifier returns tags — partial state is persisted", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(null) };
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(["SHOOTING-STAR", "SPX", "EQUITIES"]) };
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    const post = { id: "p1", title: "x", content: "x", images: ["/media/p1.png"] };
    const updated = await hydrateTagsDual([post], { textTagger, visionTagger });

    expect(updated).toBe(true);
    expect((post as any).tags_text).toBeUndefined();
    expect((post as any).tags_vision).toEqual(["SHOOTING-STAR", "SPX", "EQUITIES"]);
    expect((post as any).tags).toEqual(["SHOOTING-STAR", "SPX", "EQUITIES", "TECHNICAL-ANALYSIS"]);
  });

  it("works with text-only configuration (vision tagger absent)", async () => {
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["A", "B", "C"]) };
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");

    const post = { id: "p1", title: "x", content: "x", images: ["/media/p1.png"] };
    await hydrateTagsDual([post], { textTagger, visionTagger: null });

    expect((post as any).tags_text).toEqual(["A", "B", "C"]);
    expect((post as any).tags_vision).toBeUndefined();
    expect((post as any).tags).toEqual(["A", "B", "C"]);
  });
});
