import { afterEach, expect, it, vi } from "vitest";
afterEach(() => vi.unstubAllEnvs());
it("vision tagging needs no local grant and retains the image on the service boundary", async () => {
  vi.stubEnv("CLAUDE_CONFIG_DIR", "/nonexistent/no-local-grant");
  vi.stubEnv("CLAUDE_CODE_OAUTH_TOKEN", "");
  const completeJson = vi.fn().mockResolvedValue({ tags: ["spx", "vol", "positioning"] });
  const { createVisionTagger } = await import("../../scripts/newsfeed/vision_tagger.js");
  const tagger = createVisionTagger({ mediaDir: "/media", getTaxonomySnapshot: async () => [], readImage: async () => Buffer.from("image"), completeJson });
  await expect(tagger.tagPost({ title: "Chart", content: "Vol", images: ["chart.png"] })).resolves.toEqual(["SPX", "VOL", "POSITIONING"]);
  expect(completeJson.mock.calls[0][0]).toMatchObject({ images: [{ label: "newsfeed-chart", data: Buffer.from("image").toString("base64"), media_type: "image/png" }], accept: "tags" });
});
it("text tagging calls the same service rather than spawning an inference process", async () => {
  const requestImpl = vi.fn().mockResolvedValue({ data: { tags: ["SPX", "VOL", "POSITIONING"] } });
  const { completeViaLadder } = await import("../../scripts/newsfeed/tagger.js");
  await expect(completeViaLadder({ instruction: "Tags", system: "System", requestImpl, spawnImpl: () => { throw new Error("Legacy spawn forbidden"); } })).resolves.toEqual({ tags: ["SPX", "VOL", "POSITIONING"] });
  expect(requestImpl).toHaveBeenCalledOnce();
});
it("retains bounded post identity on the gateway request", async () => {
  const requestImpl = vi.fn().mockResolvedValue({ data: { tags: ["A", "B", "C"] } });
  const { completeViaLadder } = await import("../../scripts/newsfeed/tagger.js");
  await completeViaLadder({ instruction: "private text", system: "System", post_id: "post-7", requestImpl });
  expect(requestImpl.mock.calls[0][1].post_id).toBe("post-7");
});
it("newsfeed tagging never persists an SLM shadow row", async () => {
  vi.resetModules();
  const execute = vi.fn().mockResolvedValue({ rowsAffected: 1 });
  const writer = await import("../../scripts/db/writer.js");
  writer.__setDbForTests({ execute } as any);
  try {
    const requestImpl = vi.fn().mockResolvedValue({ data: { tags: ["A", "B", "C"] }, shadow: { post_id: "p1", mode: "shadow" } });
    const { completeViaLadder } = await import("../../scripts/newsfeed/tagger.js");
    await expect(completeViaLadder({ instruction: "text", system: "System", post_id: "p1", requestImpl })).resolves.toEqual({ tags: ["A", "B", "C"] });
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(execute).not.toHaveBeenCalled();
    expect("recordSlmShadow" in writer).toBe(false);
  } finally { writer.__resetDbForTests(); }
});
