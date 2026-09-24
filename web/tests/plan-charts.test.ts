/**
 * A text-only research finding is the chart input. Numbers that are not
 * in the title or body are not plotted. A source figure means the post
 * keeps that figure and gets no generated chart.
 */

import { describe, expect, it } from "vitest";

import { chartsForPost, planCharts } from "../lib/planCharts";

const GS = [
  "Rates are getting into the AI credit story, GS Johnstone notes. The 10-year is back above 5.10% and the 2-year sits at 4.90% after the hot September PMI, oil and the hawkish Fed tone. The desk says the speed is what matters: the 10-year is up ~25 bp in two weeks and ~35 bp over the past month, which is close to where equities usually start to care, especially with the S&P still trading around ~19x.",
  "The clearest stress test is SoftBank. Its ~$11.1bn HY raise to fund the final $10bn OpenAI tranche and refinance bridge debt cleared with strong demand, but at record dollar yields of 8.6-9.75%. That is the tell for AI-linked credit. Earlier in the week, CoreWeave's Virginia data-centre financing was still marketing in the low- to mid-9% area. The desk's read: markets will still fund AI infrastructure and neocloud project finance, even with pre-revenue construction risk. But they are not doing it cheap. Credit investors only want near-10% coupons.",
  "The desk prices a 71% chance of an October hike and about 36 bp of tightening by year-end.",
].join("\n\n");

describe("planCharts", () => {
  it("plans the GS finding as a yield chart and a separate rates chart", () => {
    const plans = planCharts(GS);
    expect(plans.map((plan) => plan.kind)).toEqual(["levels", "move"]);

    const levels = plans[0];
    expect(levels.kind).toBe("levels");
    if (levels.kind !== "levels") return;
    expect(levels.title).toBe("Dollar yields");
    expect(levels.axis).toEqual([0, 12]);
    expect(levels.dek).toBe("S&P near 19x.");
    expect(levels.reference).toEqual({ value: 10, label: "near 10%" });
    expect(levels.marks.map((mark) => [mark.label, mark.kind, mark.low, mark.high])).toEqual([
      ["SoftBank HY", "printed-range", 8.6, 9.75],
      ["CoreWeave VA", "desk-band", 9, 9.5],
      ["US 10-year", "point", 5.1, 5.1],
      ["US 2-year", "point", 4.9, 4.9],
    ]);
    expect(levels.marks.find((mark) => mark.label === "SoftBank HY")?.detail).toBe("$11.1bn");

    const move = plans[1];
    expect(move.kind).toBe("move");
    if (move.kind !== "move") return;
    expect(move.axisMax).toBe(40);
    expect(move.bars.map((bar) => [bar.label, bar.bp])).toEqual([
      ["1 month", 35],
      ["2 weeks", 25],
    ]);
    expect(move.probability).toEqual({ label: "October hike", pct: 71 });
    expect(move.readout).toEqual({ label: "Year-end tightening", bp: 36 });
    expect(move.note).toMatch(/Not additive/);
  });

  it("charts one printed range and refuses a lone point or a lone basis-point change", () => {
    const range = planCharts("SoftBank cleared at 8.6-9.75%.");
    expect(range).toHaveLength(1);
    expect(range[0].kind).toBe("levels");

    expect(planCharts("The 10-year closed at 4.2%.")).toEqual([]);
    expect(planCharts("The 10-year rose 12bp in two weeks.")).toEqual([]);
    expect(planCharts("About 36 bp of tightening by year-end.")).toEqual([]);
    expect(planCharts("Foreign investors bought $45bn of US equities. Official investors added $12bn.")).toEqual([]);
  });

  it("charts two yield levels from zero with headroom, and a two-window move on its own", () => {
    const levels = planCharts("Name A yields 3.5-4.0%. Name B sits at 2.1%.");
    expect(levels).toHaveLength(1);
    expect(levels[0].kind).toBe("levels");
    if (levels[0].kind !== "levels") return;
    expect(levels[0].axis).toEqual([0, 6]);
    expect(levels[0].marks.map((mark) => mark.label)).toEqual(["Name A", "Name B"]);

    const move = planCharts("The 10-year rose 12bp in two weeks and 18bp over the past month.");
    expect(move).toHaveLength(1);
    expect(move[0].kind).toBe("move");
    if (move[0].kind !== "move") return;
    expect(move[0].axisMax).toBe(20);
    expect(move[0].bars.map((bar) => bar.bp)).toEqual([18, 12]);
    expect(move[0].probability).toBeUndefined();
    expect(move[0].readout).toBeUndefined();
  });

  it("keeps a hike probability when there is no yield change to plot", () => {
    const plans = planCharts("Markets price a 71% chance of an October hike.");
    expect(plans).toHaveLength(1);
    expect(plans[0].kind).toBe("move");
    if (plans[0].kind !== "move") return;
    expect(plans[0].bars).toEqual([]);
    expect(plans[0].probability).toEqual({ label: "October hike", pct: 71 });
  });

  it("does not chart a dropbox post that already has a source figure", () => {
    const post = {
      title: "SoftBank",
      content: GS,
      images: ["/api/newsfeed/research/files/" + "a".repeat(64) + ".png"],
      source: {
        kind: "dropbox" as const,
        publisher: "Goldman Sachs",
        url: "/api/newsfeed/research/files/" + "b".repeat(64) + ".pdf",
        documentDate: "2026-09-24",
        folderDate: "2026-09-24",
        pages: [2],
        figures: [{ url: "/api/newsfeed/research/files/" + "a".repeat(64) + ".png", page: 2, caption: "Printed chart" }],
        fileId: "id",
        revision: "r",
        contentHash: "c".repeat(64),
      },
    };
    expect(chartsForPost(post)).toEqual([]);
    expect(chartsForPost({ title: "SoftBank", content: GS })).toEqual([]);
    expect(chartsForPost({ title: "SoftBank", content: GS, source: { ...post.source, figures: [] }, images: [] })).toHaveLength(2);
  });
});
