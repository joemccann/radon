import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  describeRunReport,
  graphRequiresConfirmation,
  listWorkflows,
  runWorkflow,
  saveWorkflow,
  toExecutorGraph,
  type RunReport,
  type WorkflowGraph,
} from "../app/workflow/workflowClient";

/**
 * F14 — workflow composer client contract.
 *
 * Pure load/save/run helpers; global fetch is stubbed so nothing hits the
 * network. We assert the request shapes the canvas sends and the report
 * interpretation it renders (including the order-confirmation gate).
 */

const SAMPLE: WorkflowGraph = {
  nodes: [
    { id: "n1", type: "data-source", params: { source: "scanner" } },
    { id: "n2", type: "filter", params: { expression: "score >= 60" } },
    { id: "n3", type: "notify", params: { channel: "service_health" } },
  ],
  edges: [
    { from: "n1", to: "n2" },
    { from: "n2", to: "n3" },
  ],
};

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy as unknown as typeof fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("toExecutorGraph", () => {
  it("maps react-flow nodes/edges into the executor shape", () => {
    const graph = toExecutorGraph(SAMPLE.nodes, SAMPLE.edges);
    expect(graph.nodes).toHaveLength(3);
    expect(graph.edges).toEqual([
      { from: "n1", to: "n2" },
      { from: "n2", to: "n3" },
    ]);
  });

  it("returns an empty graph for no nodes", () => {
    expect(toExecutorGraph([], [])).toEqual({ nodes: [], edges: [] });
  });
});

describe("listWorkflows", () => {
  it("GETs /api/workflow and returns the graphs array", async () => {
    fetchSpy.mockResolvedValue(jsonResponse({ graphs: [{ id: "g1", name: "x", graph: SAMPLE }] }));
    const graphs = await listWorkflows();
    expect(fetchSpy).toHaveBeenCalledWith("/api/workflow", { cache: "no-store" });
    expect(graphs).toHaveLength(1);
    expect(graphs[0].name).toBe("x");
  });
});

describe("saveWorkflow", () => {
  it("POSTs name + graph and returns the assigned id", async () => {
    fetchSpy.mockResolvedValue(jsonResponse({ ok: true, id: "g-new" }));
    const id = await saveWorkflow("My pipeline", SAMPLE);
    expect(id).toBe("g-new");
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/workflow");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(init.body as string);
    expect(sent.name).toBe("My pipeline");
    expect(sent.graph.nodes).toHaveLength(3);
  });

  it("throws on a non-ok response", async () => {
    fetchSpy.mockResolvedValue(jsonResponse({ error: "boom" }, false, 500));
    await expect(saveWorkflow("x", SAMPLE)).rejects.toThrow(/save failed/);
  });
});

describe("runWorkflow", () => {
  it("POSTs the graph to /api/workflow/run and returns the report", async () => {
    const report: RunReport = {
      ok: true,
      blocked_by: null,
      blocked_gate: null,
      requires_confirmation: false,
      steps: [{ node_id: "n1", node_type: "data-source", rows_in: 0, rows_out: 2, blocked: false, info: {} }],
      final_rows: [],
    };
    fetchSpy.mockResolvedValue(jsonResponse(report));
    const out = await runWorkflow(SAMPLE);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/workflow/run");
    const sent = JSON.parse(init.body as string);
    expect(sent.confirm_order).toBe(false);
    expect(out.ok).toBe(true);
  });

  it("passes confirm_order when the operator confirms", async () => {
    fetchSpy.mockResolvedValue(
      jsonResponse({ ok: true, blocked_by: null, blocked_gate: null, requires_confirmation: false, steps: [], final_rows: [] }),
    );
    await runWorkflow(SAMPLE, true);
    const sent = JSON.parse(fetchSpy.mock.calls[0][1].body as string);
    expect(sent.confirm_order).toBe(true);
  });
});

describe("graphRequiresConfirmation", () => {
  it("is true when an order node is present", () => {
    const withOrder: WorkflowGraph = {
      nodes: [...SAMPLE.nodes, { id: "n4", type: "order", params: { structure: "long_call" } }],
      edges: SAMPLE.edges,
    };
    expect(graphRequiresConfirmation(withOrder)).toBe(true);
  });

  it("is false for a notify-only pipeline", () => {
    expect(graphRequiresConfirmation(SAMPLE)).toBe(false);
  });
});

describe("describeRunReport", () => {
  it("summarises a clean run", () => {
    const report = { ok: true, blocked_by: null, blocked_gate: null, requires_confirmation: false, steps: [{}, {}, {}], final_rows: [] } as unknown as RunReport;
    expect(describeRunReport(report)).toMatch(/3 nodes/);
  });

  it("names the failing gate", () => {
    const report = { ok: false, blocked_by: "n2", blocked_gate: "convexity", requires_confirmation: false, steps: [], final_rows: [] } as RunReport;
    expect(describeRunReport(report)).toMatch(/convexity gate failed/);
  });

  it("flags a confirmation block", () => {
    const report = { ok: false, blocked_by: "n4", blocked_gate: null, requires_confirmation: true, steps: [], final_rows: [] } as RunReport;
    expect(describeRunReport(report)).toMatch(/confirmation required/);
  });
});
