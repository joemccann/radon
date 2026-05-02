import { describe, expect, it } from "vitest";

describe("enrichWithParentTags", () => {
  it("appends TECHNICAL-ANALYSIS when a candlestick pattern is present", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    expect(enrichWithParentTags(["SHOOTING-STAR", "INVERSE-HAMMER", "SPX"])).toEqual([
      "SHOOTING-STAR",
      "INVERSE-HAMMER",
      "SPX",
      "TECHNICAL-ANALYSIS",
    ]);
  });

  it("appends TECHNICAL-ANALYSIS when an indicator is present (RSI, MACD, BOLLINGER-BANDS)", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    expect(enrichWithParentTags(["RSI", "DIVERGENCE", "EQUITIES"])).toEqual([
      "RSI",
      "DIVERGENCE",
      "EQUITIES",
      "TECHNICAL-ANALYSIS",
    ]);
    expect(enrichWithParentTags(["MACD", "SPY"])).toEqual(["MACD", "SPY", "TECHNICAL-ANALYSIS"]);
    expect(enrichWithParentTags(["BOLLINGER-BANDS"])).toEqual(["BOLLINGER-BANDS", "TECHNICAL-ANALYSIS"]);
  });

  it("appends TECHNICAL-ANALYSIS for chart patterns (HEAD-SHOULDERS, BREAKOUT, WEDGE)", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    expect(enrichWithParentTags(["HEAD-SHOULDERS", "SPX"])).toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["BREAKOUT", "EQUITIES"])).toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["WEDGE"])).toContain("TECHNICAL-ANALYSIS");
  });

  it("appends TECHNICAL-ANALYSIS for unambiguous price-action concepts (SUPPORT, RESISTANCE, DIVERGENCE)", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    expect(enrichWithParentTags(["SUPPORT", "EQUITIES"])).toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["RESISTANCE"])).toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["DIVERGENCE"])).toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["TRENDLINE"])).toContain("TECHNICAL-ANALYSIS");
  });

  it("does NOT append TECHNICAL-ANALYSIS for ambiguous tags (MOMENTUM, TREND, MEAN-REVERSION, RANGE, PIVOT)", async () => {
    // Excluded because they're regularly used in factor-investing / quant /
    // macro contexts in this corpus, not just TA. A genuinely TA-focused post
    // still triggers via RSI / DIVERGENCE / SUPPORT / RESISTANCE / candlestick.
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    expect(enrichWithParentTags(["MOMENTUM", "FACTOR", "SEMIS"])).not.toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["TREND", "CTAS"])).not.toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["MEAN-REVERSION"])).not.toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["RANGE"])).not.toContain("TECHNICAL-ANALYSIS");
    expect(enrichWithParentTags(["PIVOT"])).not.toContain("TECHNICAL-ANALYSIS");
  });

  it("does NOT append TECHNICAL-ANALYSIS when no TA child is present", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    expect(enrichWithParentTags(["EQUITIES", "MARKET-STRUCTURE", "POSITIONING"])).toEqual([
      "EQUITIES",
      "MARKET-STRUCTURE",
      "POSITIONING",
    ]);
    expect(enrichWithParentTags(["MACRO", "FED", "RATES"])).toEqual(["MACRO", "FED", "RATES"]);
    expect(enrichWithParentTags(["BTC", "CRYPTO", "VALUATIONS"])).toEqual(["BTC", "CRYPTO", "VALUATIONS"]);
  });

  it("MoMo-basket regression: factor-investing post does NOT enrich (false-positive guard)", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    // Real post: "What's in the MoMo basket?" — momentum factor tilt, NOT TA.
    expect(enrichWithParentTags(["MOMENTUM", "COMPUTE", "COMMODITIES", "FACTOR", "SEMIS"])).toEqual([
      "MOMENTUM",
      "COMPUTE",
      "COMMODITIES",
      "FACTOR",
      "SEMIS",
    ]);
  });

  it("is idempotent — running twice yields the same result", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    const once = enrichWithParentTags(["SHOOTING-STAR", "SPX"]);
    const twice = enrichWithParentTags(once);
    expect(twice).toEqual(once);
    expect(twice).toEqual(["SHOOTING-STAR", "SPX", "TECHNICAL-ANALYSIS"]);
  });

  it("does not duplicate TECHNICAL-ANALYSIS when the model already picked it", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    expect(enrichWithParentTags(["TECHNICAL-ANALYSIS", "SHOOTING-STAR", "SPX"])).toEqual([
      "TECHNICAL-ANALYSIS",
      "SHOOTING-STAR",
      "SPX",
    ]);
  });

  it("preserves order — primary tags lead, parent appended at end", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    const out = enrichWithParentTags(["MOMENTUM", "EQUITIES", "BREAKOUT"]);
    expect(out).toEqual(["MOMENTUM", "EQUITIES", "BREAKOUT", "TECHNICAL-ANALYSIS"]);
    // TECHNICAL-ANALYSIS comes last, not interleaved.
    expect(out.indexOf("TECHNICAL-ANALYSIS")).toBe(out.length - 1);
  });

  it("triggers on multiple TA children but adds the parent only once", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    const out = enrichWithParentTags(["RSI", "MACD", "DIVERGENCE", "OVERSOLD"]);
    expect(out.filter((t) => t === "TECHNICAL-ANALYSIS")).toHaveLength(1);
  });

  it("handles empty/missing input safely", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    expect(enrichWithParentTags([])).toEqual([]);
    expect(enrichWithParentTags(undefined as unknown as string[])).toEqual([]);
    expect(enrichWithParentTags(null as unknown as string[])).toEqual([]);
  });

  it("does not mutate the input array", async () => {
    const { enrichWithParentTags } = await import("../../scripts/newsfeed/tag_hierarchy.js");
    const input = ["SHOOTING-STAR", "SPX"];
    const out = enrichWithParentTags(input);
    expect(input).toEqual(["SHOOTING-STAR", "SPX"]);
    expect(out).not.toBe(input);
  });
});

describe("hydrateTagsDual integration with parent enrichment", () => {
  it("union has TECHNICAL-ANALYSIS appended when a TA child appears in either arm", async () => {
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");
    const textTagger = { tagPost: async () => ["EQUITIES", "MARKET-STRUCTURE", "POSITIONING"] };
    const visionTagger = { tagPost: async () => ["SHOOTING-STAR", "SPX", "RESISTANCE"] };

    const post = {
      id: "p1",
      title: "Shooting star",
      content: "x",
      images: ["/media/p1.png"],
    };
    await hydrateTagsDual([post], { textTagger, visionTagger });

    expect((post as any).tags_text).toEqual(["EQUITIES", "MARKET-STRUCTURE", "POSITIONING"]);
    expect((post as any).tags_vision).toEqual(["SHOOTING-STAR", "SPX", "RESISTANCE"]);
    expect((post as any).tags).toEqual([
      "EQUITIES",
      "MARKET-STRUCTURE",
      "POSITIONING",
      "SHOOTING-STAR",
      "SPX",
      "RESISTANCE",
      "TECHNICAL-ANALYSIS",
    ]);
  });

  it("union does NOT contain TECHNICAL-ANALYSIS when neither arm has a TA child", async () => {
    const { hydrateTagsDual } = await import("../../scripts/newsfeed/vision_tagger.js");
    const textTagger = { tagPost: async () => ["MACRO", "FED", "RATES"] };
    const visionTagger = { tagPost: async () => ["BONDS", "YIELDS", "EUROPE"] };

    const post = { id: "p1", title: "x", content: "x", images: ["/media/p1.png"] };
    await hydrateTagsDual([post], { textTagger, visionTagger });

    expect((post as any).tags).not.toContain("TECHNICAL-ANALYSIS");
  });
});
