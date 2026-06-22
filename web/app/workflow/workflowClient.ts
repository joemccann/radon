// F14 — pure client helpers for the workflow composer.
//
// Kept free of React / React Flow so the load/save/run contract is unit-testable
// in jsdom without mounting the canvas. The composer component wires these to
// React Flow nodes/edges and the toolbar buttons.

export type WorkflowNode = {
  id: string;
  type: string;
  params: Record<string, unknown>;
  position?: { x: number; y: number };
};

export type WorkflowEdge = { from: string; to: string };

export type WorkflowGraph = {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
};

export type SavedWorkflow = {
  id: string;
  name: string;
  graph: WorkflowGraph;
  updated_at?: string;
  last_run_ok?: number | null;
};

export type RunStep = {
  node_id: string;
  node_type: string;
  rows_in: number;
  rows_out: number;
  blocked: boolean;
  info: Record<string, unknown>;
};

export type RunReport = {
  ok: boolean;
  blocked_by: string | null;
  blocked_gate: string | null;
  requires_confirmation: boolean;
  steps: RunStep[];
  final_rows: Record<string, unknown>[];
};

const EMPTY_GRAPH: WorkflowGraph = { nodes: [], edges: [] };

export async function listWorkflows(): Promise<SavedWorkflow[]> {
  const res = await fetch("/api/workflow", { cache: "no-store" });
  if (!res.ok) throw new Error(`list failed: HTTP ${res.status}`);
  const body = (await res.json()) as { graphs?: SavedWorkflow[] };
  return body.graphs ?? [];
}

export async function saveWorkflow(
  name: string,
  graph: WorkflowGraph,
  id?: string,
): Promise<string> {
  const res = await fetch("/api/workflow", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, name, graph }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`save failed: HTTP ${res.status} ${detail}`);
  }
  const body = (await res.json()) as { id: string };
  return body.id;
}

export async function runWorkflow(
  graph: WorkflowGraph,
  confirmOrder = false,
): Promise<RunReport> {
  const res = await fetch("/api/workflow/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph, confirm_order: confirmOrder }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`run failed: HTTP ${res.status} ${detail}`);
  }
  return (await res.json()) as RunReport;
}

// A graph that has an order node requires the operator to confirm before the
// run can place orders — the OrderRiskGate confirmation seam on the client.
export function graphRequiresConfirmation(graph: WorkflowGraph): boolean {
  return graph.nodes.some((node) => node.type === "order");
}

// Translate React Flow's node/edge model into the executor's graph shape.
export function toExecutorGraph(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
): WorkflowGraph {
  if (nodes.length === 0) return EMPTY_GRAPH;
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type,
      params: n.params ?? {},
      position: n.position,
    })),
    edges: edges.map((e) => ({ from: e.from, to: e.to })),
  };
}

// A short, human-readable run summary for the status strip.
export function describeRunReport(report: RunReport): string {
  if (report.ok) {
    return `Ran clean through ${report.steps.length} node${report.steps.length === 1 ? "" : "s"}`;
  }
  if (report.requires_confirmation) {
    return `Blocked at ${report.blocked_by ?? "order node"} — confirmation required`;
  }
  if (report.blocked_gate) {
    return `Blocked at ${report.blocked_by} — ${report.blocked_gate} gate failed`;
  }
  return `Blocked at ${report.blocked_by ?? "a node"}`;
}
