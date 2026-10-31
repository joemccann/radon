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
it("retains bounded post identity and writes returned SLM comparison once", async () => {
  const row = { post_id: "post-7", mode: "shadow", tags_slm: ["A", "B", "C"], tags_ladder: ["A", "B", "C"], exact3: 1, jaccard: 1 };
  const requestImpl = vi.fn().mockResolvedValue({ data: { tags: ["A", "B", "C"] }, shadow: row });
  const writeShadow = vi.fn().mockResolvedValue(true);
  const { completeViaLadder } = await import("../../scripts/newsfeed/tagger.js");
  await completeViaLadder({ instruction: "private text", system: "System", post_id: "post-7", requestImpl, writeShadow });
  expect(requestImpl.mock.calls[0][1].post_id).toBe("post-7");
  expect(writeShadow).toHaveBeenCalledExactlyOnceWith(row);
});
it("shadow write failure never discards successful source tags", async () => {
  const requestImpl = vi.fn().mockResolvedValue({ data: { tags: ["A", "B", "C"] }, shadow: { mode: "shadow" } });
  const writeShadow = vi.fn().mockRejectedValue(new Error("private db diagnostic"));
  const { completeViaLadder } = await import("../../scripts/newsfeed/tagger.js");
  await expect(completeViaLadder({ instruction: "text", system: "System", requestImpl, writeShadow })).resolves.toEqual({ tags: ["A", "B", "C"] });
  expect(writeShadow).toHaveBeenCalledOnce();
});
it("persists every original SLM comparison column through the existing bounded DB writer", async () => {
  const writer = await import("../../scripts/db/writer.js");
  const execute = vi.fn().mockResolvedValue({ rowsAffected: 1 });
  writer.__setDbForTests({ execute } as any);
  const row = { post_id: "p1", observed_at: "2026-10-30T18:00:00Z", model_version: "1.0.0", mode: "prefer", tags_slm: ["A", "B", "C"], tags_slm_raw: '{"tags":["A","B","C"]}', tags_ladder: ["A", "B", "D"], ladder_provider: "grok", ladder_sampled: 1, slm_status: "ok", slm_latency_ms: 10, ladder_latency_ms: 20, exact3: 0, jaccard: 0.5 };
  try {
    await expect(writer.recordSlmShadow(row)).resolves.toBe(true);
    expect(execute.mock.calls[0][0].sql).toContain("slm_tagger_shadow");
    expect(execute.mock.calls[0][0].args).toEqual(["p1", row.observed_at, "1.0.0", "prefer", JSON.stringify(row.tags_slm), row.tags_slm_raw, JSON.stringify(row.tags_ladder), "grok", 1, "ok", 10, 20, 0, 0.5]);
  } finally { writer.__resetDbForTests(); }
});
