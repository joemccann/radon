import { describe, expect, it } from "vitest";

import { validateWorkflowGraph } from "@/lib/workflow/validateGraph";

const node = (id: string, params: Record<string, unknown> = {}) => ({
  id,
  type: "data-source",
  params,
});

describe("workflow graph security budgets", () => {
  it("accepts a small acyclic graph", () => {
    expect(validateWorkflowGraph({
      nodes: [node("a"), node("b")],
      edges: [{ from: "a", to: "b" }],
    })).toEqual({ ok: true });
  });

  it("rejects oversized node, edge, and serialized input budgets", () => {
    expect(validateWorkflowGraph({
      nodes: Array.from({ length: 33 }, (_, index) => node(`n${index}`)),
      edges: [],
    })).toEqual({ ok: false, message: "graph exceeds 32 nodes" });

    expect(validateWorkflowGraph({
      nodes: [node("a")],
      edges: Array.from({ length: 65 }, () => ({ from: "a", to: "a" })),
    })).toEqual({ ok: false, message: "graph exceeds 64 edges" });

    expect(validateWorkflowGraph({
      nodes: [node("a", { expression: "x".repeat(70_000) })],
      edges: [],
    })).toEqual({ ok: false, message: "graph exceeds 65536 bytes" });
  });

  it("rejects duplicate ids, dangling edges, and cycles", () => {
    expect(validateWorkflowGraph({ nodes: [node("a"), node("a")], edges: [] }).ok).toBe(false);
    expect(validateWorkflowGraph({ nodes: [node("a")], edges: [{ from: "a", to: "missing" }] }).ok).toBe(false);
    expect(validateWorkflowGraph({
      nodes: [node("a"), node("b")],
      edges: [{ from: "a", to: "b" }, { from: "b", to: "a" }],
    }).ok).toBe(false);
  });

  it("rejects invalid node shapes and excessive dependency depth", () => {
    expect(validateWorkflowGraph({
      nodes: [{ id: "a", type: "data-source", params: { nested: { value: true } } }],
      edges: [],
    }).ok).toBe(false);

    const nodes = Array.from({ length: 18 }, (_, index) => ({
      ...node(`n${index}`),
      type: "filter",
    }));
    const edges = nodes.slice(1).map((_, index) => ({ from: `n${index}`, to: `n${index + 1}` }));
    expect(validateWorkflowGraph({ nodes, edges })).toEqual({
      ok: false,
      message: "graph exceeds dependency depth 16",
    });
  });
});
