import { afterEach, describe, expect, it, vi } from "vitest";
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const TAXONOMY = ["BTC", "VOL", "POSITIONING", "EQUITIES"];
const MEDIA_DIR = "/fake/public/media";
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
  it("resolves absolute, leading-slash and bare relative paths into mediaDir", async () => {
    const { __resolveImageAbsolutePath } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__resolveImageAbsolutePath({ images: ["/media/x.png"] }, "/root/media")).toBe("/root/media/x.png");
    expect(__resolveImageAbsolutePath({ images: ["media/x.png"] }, "/root/media")).toBe("/root/media/x.png");
    expect(__resolveImageAbsolutePath({ images: ["x.png"] }, "/root/media")).toBe("/root/media/x.png");
    expect(
      __resolveImageAbsolutePath({ images: ["https://media.radon.run/x.png"] }, "/root/media"),
    ).toBe("/root/media/x.png");
  });

  it("returns null for empty/missing images", async () => {
    const { __resolveImageAbsolutePath } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(__resolveImageAbsolutePath({}, "/root/media")).toBeNull();
    expect(__resolveImageAbsolutePath({ images: [] }, "/root/media")).toBeNull();
    expect(__resolveImageAbsolutePath({ images: [""] }, "/root/media")).toBeNull();
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


describe("shared vision tagging safety and deadlines", () => {
  it("a stuck gateway settles one post and does not abort the next post", async () => {
    const completeJson = vi.fn().mockImplementationOnce(() => new Promise(() => {})).mockResolvedValueOnce({ tags: ["SPX", "VOL", "EQUITIES"] });
    const { createVisionTagger, hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({ mediaDir: MEDIA_DIR, getTaxonomySnapshot: async () => TAXONOMY, readImage: async () => Buffer.from("image"), completeJson, timeoutMs: 20 });
    const posts = [{ id: "stuck", images: ["one.png"] }, { id: "next", images: ["two.png"] }];
    await expect(hydrateTagsDual(posts, { visionTagger: tagger })).resolves.toBe(true);
    expect((posts[0] as any).tags_vision).toBeUndefined();
    expect((posts[1] as any).tags_vision).toEqual(["SPX", "VOL", "EQUITIES"]);
    expect(completeJson.mock.calls[0][1].signal.aborted).toBe(true);
  });
  it("keeps the default gateway deadline at 30 seconds", async () => {
    const completeJson = vi.fn().mockResolvedValue({ tags: ["SPX", "VOL", "EQUITIES"] });
    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({ mediaDir: MEDIA_DIR, getTaxonomySnapshot: async () => TAXONOMY, readImage: async () => Buffer.from("image"), completeJson });
    await tagger.tagPost({ images: ["chart.png"] });
    expect(completeJson.mock.calls[0][1].timeoutMs).toBe(30000);
  });
  it("skips missing, unsupported and oversized image bytes without inference", async () => {
    const completeJson = vi.fn();
    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    const tagger = createVisionTagger({ mediaDir: MEDIA_DIR, getTaxonomySnapshot: async () => TAXONOMY, readImage: async () => Buffer.alloc(8 * 1024 * 1024 + 1), completeJson });
    await expect(tagger.tagPost({ images: [] })).resolves.toBeNull();
    await expect(tagger.tagPost({ images: ["chart.svg"] })).resolves.toBeNull();
    await expect(tagger.tagPost({ images: ["chart.png"] })).resolves.toBeNull();
    expect(completeJson).not.toHaveBeenCalled();
  });
  it("requires its local media root and taxonomy callback", async () => {
    const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
    expect(() => createVisionTagger()).toThrow(/mediaDir/);
    expect(() => createVisionTagger({ mediaDir: MEDIA_DIR })).toThrow(/callback/);
  });
});
