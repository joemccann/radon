/**
 * Pure derivations feeding the four remaining agent primitives.
 *
 * Each of these replaces what would otherwise be placeholder props: the trace
 * comes from the assistant loop's real tool telemetry, sources from the real
 * two-stage tagging pipeline, tasks from the executor's real RunReport, and the
 * scanner proposal from the engine's own verdict + gate map.
 *
 * The invariant worth protecting across all four: when the underlying data is
 * absent or not actionable, these return empty/null so the surface renders
 * nothing — never a fabricated citation, score, or run.
 */

import { describe, expect, it } from "vitest";

import { buildTurnSteps, describeEngines, describeTool, formatElapsed } from "../lib/agent/turnSteps";
import { buildAnalysisSources, buildFollowUps } from "../lib/agent/analysisSources";
import { graphToRunningTasks, runReportToTasks } from "../lib/agent/workflowTasks";
import { buildScannerProposal, isActionable } from "../lib/agent/scannerProposal";
import type { RunReport } from "../app/workflow/workflowClient";
import type { AssistantToolEvent, ThetaHarvesterResult, ThetaHarvesterStructure } from "../lib/types";

// ── EngineTrace ────────────────────────────────────────────────────────────

describe("buildTurnSteps", () => {
  const flow: AssistantToolEvent = { name: "get_flow", input: {}, ok: true };
  const gex: AssistantToolEvent = { name: "get_gex", input: {}, ok: true };

  it("shows a single running step before any tool has run", () => {
    const steps = buildTurnSteps([], "submitted");
    expect(steps).toHaveLength(1);
    expect(steps[0].label).toBe("Routing request");
    expect(steps[0].state).toBe("running");
  });

  it("renames the trailing step once tools have returned", () => {
    const steps = buildTurnSteps([flow], "submitted");
    expect(steps[steps.length - 1].label).toBe("Reasoning over tool results");
  });

  it("maps each tool event to a settled step in execution order", () => {
    const steps = buildTurnSteps([flow, gex], "streaming");
    expect(steps.map((s) => s.label)).toEqual([
      "Read dark-pool flow",
      "Read gamma exposure",
      "Composing response",
    ]);
    expect(steps[0].state).toBe("done");
    expect(steps[1].state).toBe("done");
  });

  it("marks a failed tool call as unsettled with FAILED meta", () => {
    const steps = buildTurnSteps([{ name: "get_flow", input: {}, ok: false, error: "boom" }], "done");
    expect(steps[0].state).toBe("waiting");
    expect(steps[0].meta).toBe("FAILED");
  });

  it("tags a repeated call as CACHED", () => {
    const steps = buildTurnSteps([{ name: "get_gex", input: {}, ok: true, repeated: true }], "done");
    expect(steps[0].meta).toBe("CACHED");
  });

  it("adds no trailing step once the turn is done", () => {
    const steps = buildTurnSteps([flow], "done");
    expect(steps).toHaveLength(1);
    expect(steps[0].state).toBe("done");
  });

  it("surfaces an error phase as its own step", () => {
    const steps = buildTurnSteps([], "error");
    expect(steps[0].meta).toBe("ERROR");
  });

  it("de-snake-cases an unknown tool rather than dropping it", () => {
    expect(describeTool("get_brand_new_thing")).toBe("Get brand new thing");
    expect(describeTool("get_flow")).toBe("Read dark-pool flow");
  });
});

describe("describeEngines / formatElapsed", () => {
  it("falls back to SPECTRAL when the model is unknown", () => {
    expect(describeEngines(null)).toEqual(["SPECTRAL"]);
    expect(describeEngines(undefined)).toEqual(["SPECTRAL"]);
  });

  it("names the provider family from the model id", () => {
    expect(describeEngines("grok-4-latest")).toEqual(["GROK"]);
    expect(describeEngines("claude-opus-5")).toEqual(["CLAUDE"]);
  });

  it("formats elapsed as mono seconds and rejects nonsense", () => {
    expect(formatElapsed(4200)).toBe("4.2S");
    expect(formatElapsed(-1)).toBe("");
    expect(formatElapsed(Number.NaN)).toBe("");
  });
});

// ── AnalysisSources ────────────────────────────────────────────────────────

describe("buildAnalysisSources", () => {
  it("returns nothing for a post with no href and no tagger output", () => {
    expect(buildAnalysisSources({})).toEqual([]);
  });

  it("lists the article host first, then whichever taggers contributed", () => {
    const sources = buildAnalysisSources({
      href: "https://www.themarketear.com/posts/abc",
      tags_text: ["MU", "FED"],
      tags_vision: ["CHART"],
    });
    expect(sources.map((s) => s.id)).toEqual(["article", "tagger-text", "tagger-vision"]);
    expect(sources[0].name).toBe("themarketear.com");
    expect(sources[1].feed).toBe("2 tags");
    expect(sources[2].feed).toBe("1 tag");
  });

  it("omits a tagger that produced nothing", () => {
    const sources = buildAnalysisSources({ href: "https://x.com/a", tags_text: ["MU"] });
    expect(sources.map((s) => s.id)).toEqual(["article", "tagger-text"]);
  });

  it("drops an unparseable href instead of rendering a broken row", () => {
    expect(buildAnalysisSources({ href: "not a url" })).toEqual([]);
  });
});

describe("buildFollowUps", () => {
  it("turns ticker-shaped tags into flow prompts", () => {
    expect(buildFollowUps({ tags: ["MU", "NVDA"] })).toEqual(["Scan MU flow", "Scan NVDA flow"]);
  });

  it("ignores prose tags and lowercase noise", () => {
    expect(buildFollowUps({ tags: ["FED RATES", "macro", "MU"] })).toEqual(["Scan MU flow"]);
  });

  it("dedupes and caps at three", () => {
    expect(buildFollowUps({ tags: ["MU", "MU", "A", "B", "C", "D"] })).toEqual([
      "Scan MU flow",
      "Scan A flow",
      "Scan B flow",
    ]);
  });

  it("returns nothing when there are no tags", () => {
    expect(buildFollowUps({})).toEqual([]);
  });
});

// ── TaskRuns ───────────────────────────────────────────────────────────────

function report(overrides: Partial<RunReport> = {}): RunReport {
  return {
    ok: true,
    blocked_by: null,
    blocked_gate: null,
    requires_confirmation: false,
    steps: [],
    final_rows: [],
    ...overrides,
  };
}

describe("runReportToTasks", () => {
  it("maps executed nodes to done with their real row counts", () => {
    const tasks = runReportToTasks(
      report({
        steps: [
          { node_id: "n1", node_type: "universe", rows_in: 0, rows_out: 34, blocked: false, info: {} },
          { node_id: "n2", node_type: "filter", rows_in: 34, rows_out: 6, blocked: false, info: {} },
        ],
      }),
    );

    expect(tasks.map((t) => t.state)).toEqual(["done", "done"]);
    expect(tasks[0].title).toBe("Universe · n1");
    expect(tasks[1].meta).toBe("6 ROWS");
    expect(tasks[1].steps).toEqual([
      { label: "rows in", meta: "34" },
      { label: "rows out", meta: "6" },
    ]);
  });

  it("leaves a blocked node queued and names the gate that stopped it", () => {
    const tasks = runReportToTasks(
      report({
        ok: false,
        blocked_by: "n2",
        blocked_gate: "convexity",
        steps: [
          { node_id: "n1", node_type: "universe", rows_in: 0, rows_out: 4, blocked: false, info: {} },
          { node_id: "n2", node_type: "order", rows_in: 4, rows_out: 0, blocked: true, info: { gate: "convexity" } },
        ],
      }),
    );

    expect(tasks[0].state).toBe("done");
    expect(tasks[1].state).toBe("queued");
    expect(tasks[1].steps).toContainEqual({ label: "gate", meta: "CONVEXITY" });
  });

  it("returns nothing for a report with no steps", () => {
    expect(runReportToTasks(report())).toEqual([]);
  });
});

describe("graphToRunningTasks", () => {
  it("marks only the first node running and queues the rest", () => {
    const tasks = graphToRunningTasks({
      nodes: [
        { id: "a", type: "universe", params: {} },
        { id: "b", type: "filter", params: {} },
        { id: "c", type: "order", params: {} },
      ],
      edges: [],
    });
    expect(tasks.map((t) => t.state)).toEqual(["running", "queued", "queued"]);
  });

  it("handles an empty graph", () => {
    expect(graphToRunningTasks({ nodes: [], edges: [] })).toEqual([]);
  });
});

// ── ProposalCard ───────────────────────────────────────────────────────────

function harvestStructure(): ThetaHarvesterStructure {
  return {
    expiry: "20260717",
    dte: 23,
    net_delta: -0.01,
    theta: 0.075,
    gamma: -0.0042,
    vega: -0.038,
    credit: 1.9,
    short_put: {
      symbol: "AAPL260717P00095000",
      expiry: "20260717",
      strike: 95,
      right: "P",
      iv: 35,
      delta: -0.15,
      theta: -0.04,
      gamma: 0.002,
      vega: 0.018,
      bid: 0.9,
      ask: 1.1,
      volume: 200,
      open_interest: 900,
    },
    short_call: {
      symbol: "AAPL260717C00105000",
      expiry: "20260717",
      strike: 105,
      right: "C",
      iv: 35,
      delta: 0.16,
      theta: -0.035,
      gamma: 0.0022,
      vega: 0.02,
      bid: 0.8,
      ask: 1.0,
      volume: 180,
      open_interest: 850,
    },
  };
}

function candidate(overrides: Partial<ThetaHarvesterResult> = {}): ThetaHarvesterResult {
  return {
    ticker: "MU",
    score: 97,
    verdict: "THETA_HARVEST",
    structure: harvestStructure(),
    spot: 142,
    iv: 0.51,
    hv20: 0.33,
    hv60: 0.35,
    iv_rv_edge: 18,
    iv_rv_ratio: 1.5,
    trend_20d_pct: 1.2,
    range_score: 82,
    dealer_support: "SUPPORT",
    net_gex: 1,
    gex_flip: 140,
    setup: "MU short strangle — IV/RV edge 18, dealer support intact.",
    gates: { convexity: true, edge: true, risk: true },
    errors: [],
    ...overrides,
  };
}

describe("isActionable", () => {
  it("accepts a THETA_HARVEST verdict with every gate passing", () => {
    expect(isActionable(candidate())).toBe(true);
  });

  it("rejects a non-harvest verdict", () => {
    expect(isActionable(candidate({ verdict: "WATCHLIST" }))).toBe(false);
  });

  it("rejects a failing gate", () => {
    expect(isActionable(candidate({ gates: { convexity: true, edge: false } }))).toBe(false);
  });

  it("rejects an empty gate map — unproven is not actionable", () => {
    expect(isActionable(candidate({ gates: {} }))).toBe(false);
  });

  it("rejects a candidate that recorded errors", () => {
    expect(isActionable(candidate({ errors: ["no chain"] }))).toBe(false);
  });
});

describe("buildScannerProposal", () => {
  it("returns null on an empty scan", () => {
    expect(buildScannerProposal([])).toBeNull();
  });

  it("returns null when the leading row is not actionable", () => {
    expect(buildScannerProposal([candidate({ verdict: "WATCHLIST" }), candidate()])).toBeNull();
  });

  it("uses the engine's absolute score as confidence, not a relative rescale", () => {
    const proposal = buildScannerProposal([candidate({ score: 82 })]);
    expect(proposal?.confidence).toBeCloseTo(0.82, 5);
  });

  it("clamps a score outside 0-100", () => {
    expect(buildScannerProposal([candidate({ score: 140 })])?.confidence).toBe(1);
    expect(buildScannerProposal([candidate({ score: -5 })])?.confidence).toBe(0);
  });

  it("carries the engine's own setup text as the statement", () => {
    const proposal = buildScannerProposal([candidate()]);
    expect(proposal?.statement).toBe("MU short strangle — IV/RV edge 18, dealer support intact.");
    expect(proposal?.ticker).toBe("MU");
  });

  it("lists the next ranked rows as alternatives, capped at three", () => {
    const proposal = buildScannerProposal([
      candidate(),
      candidate({ ticker: "NVDA", score: 91 }),
      candidate({ ticker: "AMD", verdict: "WATCHLIST" }),
      candidate({ ticker: "INTC", score: 88 }),
      candidate({ ticker: "QCOM", score: 87 }),
    ]);
    expect(proposal?.alternatives).toHaveLength(3);
    expect(proposal?.alternatives[0]).toEqual({
      id: "NVDA",
      label: "NVDA SHORT 95P / 105C",
      meta: "SCORE 91",
    });
    expect(proposal?.alternatives[1].meta).toBe("WATCHLIST");
  });

  it("never interpolates a structure object as [object Object] in labels or fallback statement", () => {
    const proposal = buildScannerProposal([
      candidate(),
      candidate({ ticker: "AMAT", score: 91 }),
      candidate({ ticker: "MSTR", score: 88 }),
      candidate({ ticker: "TTWO", score: 87 }),
    ]);
    expect(proposal).not.toBeNull();
    for (const alt of proposal!.alternatives) {
      expect(alt.label).toMatch(/^(AMAT|MSTR|TTWO) SHORT 95P \/ 105C$/);
      expect(alt.label).not.toContain("[object Object]");
    }

    const fallback = buildScannerProposal([candidate({ setup: "   " })]);
    expect(fallback?.statement).not.toContain("[object Object]");
    expect(fallback?.statement).toContain("MU SHORT 95P / 105C");
  });

  it("uses ticker + formatted structure when setup is empty", () => {
    const proposal = buildScannerProposal([candidate({ setup: "" })]);
    expect(proposal?.statement).toBe("MU SHORT 95P / 105C — IV/RV edge 18.0, range score 82.");
    expect(proposal?.statement).not.toContain("[object Object]");
  });
});
