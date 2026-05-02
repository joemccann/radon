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

describe("createTaggerRouter", () => {
  it("uses vision tagger when post has images", async () => {
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(["A", "B", "C"]) };
    const textTagger = { tagPost: vi.fn() };

    const { createTaggerRouter } = await import("../../scripts/newsfeed/vision_tagger.js");
    const router = createTaggerRouter({ visionTagger, textTagger });

    const tags = await router.tagPost({ id: "p1", images: ["/media/p1.png"], title: "x", content: "x" });

    expect(tags).toEqual(["A", "B", "C"]);
    expect(visionTagger.tagPost).toHaveBeenCalledTimes(1);
    expect(textTagger.tagPost).not.toHaveBeenCalled();
  });

  it("falls back to text tagger when vision returns null", async () => {
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(null) };
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["X", "Y", "Z"]) };

    const { createTaggerRouter } = await import("../../scripts/newsfeed/vision_tagger.js");
    const router = createTaggerRouter({ visionTagger, textTagger });

    const tags = await router.tagPost({ id: "p2", images: ["/media/p2.png"], title: "x", content: "x" });

    expect(tags).toEqual(["X", "Y", "Z"]);
    expect(visionTagger.tagPost).toHaveBeenCalledTimes(1);
    expect(textTagger.tagPost).toHaveBeenCalledTimes(1);
  });

  it("uses text tagger when post has no image", async () => {
    const visionTagger = { tagPost: vi.fn() };
    const textTagger = { tagPost: vi.fn().mockResolvedValue(["MACRO", "FED", "RATES"]) };

    const { createTaggerRouter } = await import("../../scripts/newsfeed/vision_tagger.js");
    const router = createTaggerRouter({ visionTagger, textTagger });

    const tags = await router.tagPost({ id: "p3", title: "x", content: "x" });

    expect(tags).toEqual(["MACRO", "FED", "RATES"]);
    expect(visionTagger.tagPost).not.toHaveBeenCalled();
    expect(textTagger.tagPost).toHaveBeenCalledTimes(1);
  });

  it("returns null when both taggers are absent", async () => {
    const { createTaggerRouter } = await import("../../scripts/newsfeed/vision_tagger.js");
    const router = createTaggerRouter({ visionTagger: null, textTagger: null });
    const tags = await router.tagPost({ id: "p4", title: "x", content: "x" });
    expect(tags).toBeNull();
  });

  it("works with vision-only (no text fallback configured)", async () => {
    const visionTagger = { tagPost: vi.fn().mockResolvedValue(["A", "B", "C"]) };
    const { createTaggerRouter } = await import("../../scripts/newsfeed/vision_tagger.js");
    const router = createTaggerRouter({ visionTagger, textTagger: null });

    const tags = await router.tagPost({ id: "p5", images: ["/media/p5.png"] });
    expect(tags).toEqual(["A", "B", "C"]);
  });
});
