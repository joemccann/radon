// @vitest-environment jsdom
//
// Wiring tests for the four agent primitives at their real integration points.
// The derivations are unit-tested in agent-derivations.test.ts; this file pins
// that each surface actually renders them, and that the ask-bus handoff from
// the newsfeed lightbox reaches the chat overlay.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { emitAsk, subscribeAsk } from "../lib/agent/askBus";
import AnalysisSources from "../components/agent/AnalysisSources";
import { buildAnalysisSources, buildFollowUps } from "../lib/agent/analysisSources";
import TaskRuns from "../components/agent/TaskRuns";
import { runReportToTasks } from "../lib/agent/workflowTasks";
import ProposalCard from "../components/agent/ProposalCard";
import { buildScannerProposal } from "../lib/agent/scannerProposal";
import type { ThetaHarvesterResult, ThetaHarvesterStructure } from "../lib/types";

afterEach(cleanup);

describe("askBus", () => {
  it("delivers an emitted prompt to a subscriber", () => {
    const seen: string[] = [];
    const unsubscribe = subscribeAsk((p) => seen.push(p));

    emitAsk("Scan MU flow");
    expect(seen).toEqual(["Scan MU flow"]);

    unsubscribe();
    emitAsk("Scan NVDA flow");
    expect(seen).toEqual(["Scan MU flow"]);
  });

  it("ignores an empty prompt", () => {
    const handler = vi.fn();
    const unsubscribe = subscribeAsk(handler);
    emitAsk("   ");
    expect(handler).not.toHaveBeenCalled();
    unsubscribe();
  });
});

describe("AnalysisSources — newsfeed wiring", () => {
  const post = {
    href: "https://www.themarketear.com/posts/abc",
    tags: ["MU", "FED RATES"],
    tags_text: ["MU"],
    tags_vision: ["CHART"],
  };

  it("renders real provenance rows derived from the post", () => {
    render(
      <AnalysisSources sources={buildAnalysisSources(post)} followUps={buildFollowUps(post)} />,
    );
    expect(screen.getByText("themarketear.com")).toBeTruthy();
    expect(screen.getByText("Text tagger")).toBeTruthy();
    expect(screen.getByText("Vision tagger")).toBeTruthy();
  });

  it("hands a clicked follow-up to the ask bus", () => {
    const received: string[] = [];
    const unsubscribe = subscribeAsk((p) => received.push(p));

    render(
      <AnalysisSources
        sources={buildAnalysisSources(post)}
        followUps={buildFollowUps(post)}
        onFollowUp={emitAsk}
      />,
    );
    fireEvent.click(screen.getByText("Scan MU flow"));

    expect(received).toEqual(["Scan MU flow"]);
    unsubscribe();
  });

  it("renders nothing for a post with no provenance and no follow-ups", () => {
    const { container } = render(
      <AnalysisSources sources={buildAnalysisSources({})} followUps={buildFollowUps({})} />,
    );
    expect(container.querySelector(".analysis-sources__label")).toBeNull();
  });
});

describe("TaskRuns — workflow wiring", () => {
  it("renders one row per executed node with its real row counts", () => {
    const tasks = runReportToTasks({
      ok: false,
      blocked_by: "n2",
      blocked_gate: "convexity",
      requires_confirmation: false,
      steps: [
        { node_id: "n1", node_type: "universe", rows_in: 0, rows_out: 34, blocked: false, info: {} },
        { node_id: "n2", node_type: "order", rows_in: 34, rows_out: 0, blocked: true, info: { gate: "convexity" } },
      ],
      final_rows: [],
    });

    render(<TaskRuns tasks={tasks} />);
    expect(screen.getByText("Universe · n1")).toBeTruthy();
    expect(screen.getByText("34 ROWS")).toBeTruthy();
    expect(screen.getByText("COMPLETED")).toBeTruthy();
    expect(screen.getByText("QUEUED")).toBeTruthy();
    expect(screen.getByText("CONVEXITY")).toBeTruthy();
  });
});

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

describe("ProposalCard — scanner wiring", () => {
  const actionable: ThetaHarvesterResult = {
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
    setup: "MU short strangle — IV/RV edge 18.",
    gates: { convexity: true, edge: true },
    errors: [],
  };

  it("renders the engine's statement and confidence meter", () => {
    const proposal = buildScannerProposal([actionable])!;
    render(
      <ProposalCard
        engines={["THETA"]}
        statement={proposal.statement}
        confidence={proposal.confidence}
        alternatives={proposal.alternatives}
        onAccept={() => {}}
        onDismiss={() => {}}
      />,
    );

    expect(screen.getByText("MU short strangle — IV/RV edge 18.")).toBeTruthy();
    expect(screen.getByText("0.97 · HIGH")).toBeTruthy();
    expect(screen.getByRole("meter").getAttribute("aria-valuenow")).toBe("97");
  });

  it("accept and dismiss are distinct actions — accept never routes an order itself", () => {
    const onAccept = vi.fn();
    const onDismiss = vi.fn();
    const proposal = buildScannerProposal([actionable])!;
    render(
      <ProposalCard
        statement={proposal.statement}
        confidence={proposal.confidence}
        onAccept={onAccept}
        onDismiss={onDismiss}
      />,
    );

    fireEvent.click(screen.getByText("Dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onAccept).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Accept"));
    expect(onAccept).toHaveBeenCalledTimes(1);
  });

  it("renders alternative labels with ticker and strike legs, not [object Object]", () => {
    const proposal = buildScannerProposal([
      { ...actionable, ticker: "MU", score: 97 },
      { ...actionable, ticker: "AMAT", score: 91, setup: "AMAT TRUE_THETA" },
      { ...actionable, ticker: "MSTR", score: 88 },
      { ...actionable, ticker: "TTWO", score: 87 },
    ])!;
    render(
      <ProposalCard
        engines={["THETA"]}
        statement={proposal.statement}
        confidence={proposal.confidence}
        alternatives={proposal.alternatives}
        onAccept={() => {}}
        onDismiss={() => {}}
      />,
    );

    expect(screen.getByText("AMAT SHORT 95P / 105C")).toBeTruthy();
    expect(screen.getByText("MSTR SHORT 95P / 105C")).toBeTruthy();
    expect(screen.getByText("TTWO SHORT 95P / 105C")).toBeTruthy();
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
  });
});

describe("EngineTrace — chat wiring", () => {
  it("shows the trace while a turn is pending and clears it when content lands", async () => {
    let resolveTurn: (value: unknown) => void = () => {};
    const pending = new Promise((resolve) => {
      resolveTurn = resolve;
    });
    const fetchMock = vi.fn(async () => {
      const body = await pending;
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    const ChatPanel = (await import("../components/ChatPanel")).default;
    const { container } = render(<ChatPanel activeSection="dashboard" />);

    fireEvent.change(screen.getByLabelText("Ask Radon"), { target: { value: "read MU flow" } });
    fireEvent.submit(screen.getByLabelText("Ask Radon").closest("form")!);

    await waitFor(() => {
      expect(container.querySelector(".engine-trace")).not.toBeNull();
    });
    expect(screen.getByText("Routing request")).toBeTruthy();

    resolveTurn({
      content: "Flow read.",
      model: "grok-4-latest",
      toolEvents: [{ name: "get_flow", input: {}, ok: true }],
    });

    await waitFor(
      () => {
        expect(container.querySelector(".engine-trace")).toBeNull();
      },
      { timeout: 4000 },
    );
  });
});
