/**
 * A text-only finding is charted only when its numbers form a series.
 * The chart type follows the series. Every plotted number has to sit
 * next to its label in the text.
 */

import { describe, expect, it } from "vitest";

import { chartsForPost, planCharts, verifyChart, type ChartPlan } from "../lib/planCharts";

const GS = [
  "Rates are getting into the AI credit story, GS Johnstone notes. The 10-year is back above 5.10% and the 2-year sits at 4.90% after the hot September PMI, oil and the hawkish Fed tone. The desk says the speed is what matters: the 10-year is up ~25 bp in two weeks and ~35 bp over the past month, which is close to where equities usually start to care, especially with the S&P still trading around ~19x.",
  "The clearest stress test is SoftBank. Its ~$11.1bn HY raise to fund the final $10bn OpenAI tranche and refinance bridge debt cleared with strong demand, but at record dollar yields of 8.6-9.75%. That is the tell for AI-linked credit. Earlier in the week, CoreWeave's Virginia data-centre financing was still marketing in the low- to mid-9% area. The desk's read: markets will still fund AI infrastructure and neocloud project finance, even with pre-revenue construction risk. But they are not doing it cheap. Credit investors only want near-10% coupons.",
  "The desk prices a 71% chance of an October hike and about 36 bp of tightening by year-end.",
].join("\n\n");

const TEN_YEAR = [
  "10-year yield is about 25 bps higher, but term premium has fallen since the buyback announcement",
  "Simon White at Bloomberg makes the useful point here: the 10-year yield is about 25 bps higher since the Treasury Secretary announced it would add to its long-dated debt buybacks. The term premium has fallen since the buyback announcement.",
].join("\n");

const MVRV = [
  "Glassnode: bitcoin MVRV crossed back above its 365-day average, the same cross seen in 2019 and 2023",
  "MVRV has crossed back above its 365-day average. After a billion-dollar ETF inflow yesterday, bitcoin was down modestly after tagging $87,000 highs overnight.",
].join("\n");

const TOKENIZED = [
  "Tokenized assets excluding stablecoins grew from ~$10bn in Jan 2025 to ~$39bn in Sep 2026, with US Treasury debt at $15bn in September 2026",
  "Deutsche Bank Research says the global tokenized assets market, excluding stablecoins, grew from ~$10bn in Jan 2025 to ~$39bn in Sep 2026. US Treasury debt is still the largest on-chain tokenized asset segment excluding stablecoins, reaching $15bn in September 2026.",
].join("\n");

const DLT = [
  "Global DLT fixed-income issuance reached €4.8bn in 2025, up 48% from 2024, finance at €3.45bn",
  "Global DLT fixed-income issuance reached €4.8bn in 2025, up 48% from 2024. Finance led global digital bond issuance by issuer type in 2025 at €3.45bn. Government issued €1.29bn. Construction was €47mn, supranational €6mn, technology €5mn and energy €2mn.",
].join("\n");

const OATS = [
  "OATs were sold a second straight week (-0.7z after -1.2z; MoM -1.1z), but Bunds have been sold more over 3m",
  "OATs were sold a second straight week (-0.7z after -1.2z; MoM -1.1z), but the 3m decline has been larger in Bunds than OATs.",
].join("\n");

function bar(plan: ChartPlan) {
  expect(plan.kind).toBe("bar");
  if (plan.kind !== "bar") throw new Error("bar");
  return plan;
}

function range(plan: ChartPlan) {
  expect(plan.kind).toBe("range");
  if (plan.kind !== "range") throw new Error("range");
  return plan;
}

describe("planCharts", () => {
  it("plots GS yields as a range and the two 10-year windows as a bar", () => {
    const plans = planCharts(GS);
    expect(plans.map((plan) => plan.kind)).toEqual(["range", "bar"]);
    const yields = range(plans[0]);
    expect(yields.title).toBe("Dollar yields");
    expect(yields.unit).toBe("%");
    expect(yields.axis).toEqual([0, 12]);
    expect(yields.reference).toEqual({ value: 10, label: "near 10%" });
    expect(yields.marks.map((mark) => [mark.label, mark.estimated, mark.low, mark.high])).toEqual([
      ["SoftBank HY", false, 8.6, 9.75],
      ["CoreWeave VA", true, 9, 9.5],
      ["10-year", false, 5.1, 5.1],
      ["2-year", false, 4.9, 4.9],
    ]);
    expect(yields.marks[0].detail).toBe("$11.1bn");
    expect(yields.sourceNote).toMatch(/Not a printed coupon/);

    const move = bar(plans[1]);
    expect(move.title).toBe("10-year change");
    expect(move.unit).toBe("bp");
    expect(move.axis).toEqual([0, 40]);
    expect(move.bars.map((row) => [row.label, row.value])).toEqual([
      ["2 weeks", 25],
      ["1 month", 35],
    ]);
    expect(move.note).toMatch(/Not additive/);
    expect(JSON.stringify(plans)).not.toMatch(/71|36/);
    expect(plans.every((plan) => verifyChart(plan, GS))).toBe(true);
  });

  it("keeps one printed range and drops a lone point, a lone move, and a lone probability", () => {
    const printed = planCharts("SoftBank cleared at 8.6-9.75%.");
    expect(printed).toHaveLength(1);
    expect(range(printed[0]).marks.map((mark) => mark.label)).toEqual(["SoftBank"]);

    expect(planCharts("The 10-year closed at 4.2%. ")).toEqual([]);
    expect(planCharts("The 10-year rose 12bp in two weeks.")).toEqual([]);
    expect(planCharts("About 36 bp of tightening by year-end.")).toEqual([]);
    expect(planCharts("Markets price a 71% chance of an October hike.")).toEqual([]);
    expect(planCharts(TEN_YEAR)).toEqual([]);
    expect(planCharts(MVRV)).toEqual([]);
  });

  it("joins a printed range and a second yield on one zero axis", () => {
    const plans = planCharts("Name A yields 3.5-4.0%. Name B sits at 2.1%.");
    expect(plans).toHaveLength(1);
    const yields = range(plans[0]);
    expect(yields.axis).toEqual([0, 6]);
    expect(yields.marks.map((mark) => mark.label)).toEqual(["Name A", "Name B"]);
  });

  it("bars two windows and two dollar flows, and does not mix them with a yield", () => {
    const move = planCharts("The 10-year rose 12bp in two weeks and 18bp over the past month.");
    expect(move).toHaveLength(1);
    expect(bar(move[0]).axis).toEqual([0, 20]);
    expect(bar(move[0]).bars.map((row) => row.value)).toEqual([12, 18]);

    const flows = planCharts("Foreign investors bought $45bn of US equities. Official investors added $12bn.");
    expect(flows).toHaveLength(1);
    expect(bar(flows[0]).unit).toBe("$bn");
    expect(bar(flows[0]).bars.map((row) => [row.label, row.value])).toEqual([
      ["Foreign investors", 45],
      ["Official investors", 12],
    ]);
  });

  it("draws the latest text-only series as a line, an issuance bar, and a z-score bar", () => {
    const growth = planCharts(TOKENIZED);
    expect(growth.map((plan) => plan.kind)).toEqual(["line"]);
    expect(growth[0].kind === "line" && growth[0].points.map((point) => [point.label, point.value])).toEqual([
      ["Jan 2025", 10],
      ["Sep 2026", 39],
    ]);
    expect(growth[0].kind === "line" && growth[0].unit).toBe("$bn");
    expect(JSON.stringify(growth)).not.toContain("15");

    const issuance = planCharts(DLT);
    expect(issuance).toHaveLength(1);
    const rows = bar(issuance[0]);
    expect(rows.unit).toBe("€bn");
    expect(rows.bars.map((row) => [row.label, row.value])).toEqual([
      ["Finance", 3.45],
      ["Government", 1.29],
      ["Construction", 0.047],
      ["Supranational", 0.006],
      ["Technology", 0.005],
      ["Energy", 0.002],
    ]);
    expect(JSON.stringify(issuance)).not.toMatch(/48|4\.8/);

    const oats = planCharts(OATS);
    expect(oats).toHaveLength(1);
    expect(bar(oats[0]).unit).toBe("z");
    expect(bar(oats[0]).bars.map((row) => [row.label, row.value])).toEqual([
      ["Week", -0.7],
      ["After", -1.2],
      ["MoM", -1.1],
    ]);
    expect(bar(oats[0]).axis[0]).toBeLessThan(0);
    expect(bar(oats[0]).axis[1]).toBe(0);
  });

  it("uses a scatter when two names each have the same pair of units", () => {
    const plans = planCharts("Fund A duration is 5 years and the yield is 4%. Fund B duration is 7 years and the yield is 6%.");
    expect(plans).toHaveLength(1);
    expect(plans[0].kind).toBe("scatter");
    if (plans[0].kind !== "scatter") return;
    expect(plans[0].points.map((point) => [point.label, point.x, point.y])).toEqual([
      ["Fund A", 5, 4],
      ["Fund B", 7, 6],
    ]);
  });

  it("rejects a chart whose label does not own the number", () => {
    const fake: ChartPlan = {
      kind: "bar",
      title: "Issuance",
      unit: "€bn",
      axis: [0, 4],
      ticks: [0, 4],
      bars: [
        { id: "energy", label: "Energy", value: 3.45 },
        { id: "finance", label: "Finance", value: 0.002 },
      ],
    };
    expect(verifyChart(fake, DLT)).toBe(false);
    expect(planCharts(DLT).every((plan) => verifyChart(plan, DLT))).toBe(true);
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
