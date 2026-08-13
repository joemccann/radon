const MAX_GRAPH_BYTES = 65_536;
const MAX_NODES = 32;
const MAX_EDGES = 64;
const MAX_DEPTH = 16;
const MAX_EXTERNAL_NODES = 8;
const MAX_PARAMS = 16;
const MAX_TEXT = 1_024;
const NODE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const NODE_TYPE = /^[a-z][a-z0-9-]{0,31}$/;
const EXTERNAL_NODE_TYPES = new Set(["data-source", "notify", "order"]);

export type WorkflowGraphValidation =
  | { ok: true }
  | { ok: false; message: string };

function reject(message: string): WorkflowGraphValidation {
  return { ok: false, message };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isBoundedScalar(value: unknown): boolean {
  return value === null
    || typeof value === "boolean"
    || (typeof value === "number" && Number.isFinite(value))
    || (typeof value === "string" && value.length <= MAX_TEXT);
}

/**
 * Validate the untrusted graph before persistence or trusted-service delegation.
 * The Python executor applies the same budgets at its work boundary; this layer
 * rejects oversized input before it consumes a backend slot.
 */
export function validateWorkflowGraph(graph: unknown): WorkflowGraphValidation {
  if (!isRecord(graph) || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    return reject("graph must have nodes[] and edges[]");
  }

  let serialized: string;
  try {
    serialized = JSON.stringify(graph);
  } catch {
    return reject("graph must be JSON serializable");
  }
  if (new TextEncoder().encode(serialized).byteLength > MAX_GRAPH_BYTES) {
    return reject(`graph exceeds ${MAX_GRAPH_BYTES} bytes`);
  }
  if (graph.nodes.length > MAX_NODES) return reject(`graph exceeds ${MAX_NODES} nodes`);
  if (graph.edges.length > MAX_EDGES) return reject(`graph exceeds ${MAX_EDGES} edges`);

  const ids = new Set<string>();
  let externalNodes = 0;
  for (const rawNode of graph.nodes) {
    if (!isRecord(rawNode)) return reject("every node must be an object");
    const { id, type, params, position } = rawNode;
    if (typeof id !== "string" || !NODE_ID.test(id)) return reject("every node needs a valid id");
    if (ids.has(id)) return reject(`duplicate node id: ${id}`);
    ids.add(id);
    if (typeof type !== "string" || !NODE_TYPE.test(type)) return reject(`node ${id} has an invalid type`);
    if (type === "order") {
      return reject("workflow order execution is disabled until a complete risk-reviewed contract editor is available");
    }
    if (EXTERNAL_NODE_TYPES.has(type)) externalNodes += 1;
    if (!isRecord(params)) return reject(`node ${id} params must be an object`);
    const entries = Object.entries(params);
    if (entries.length > MAX_PARAMS || entries.some(([key, value]) => !NODE_ID.test(key) || !isBoundedScalar(value))) {
      return reject(`node ${id} params exceed scalar bounds`);
    }
    if (position !== undefined) {
      if (!isRecord(position)
        || typeof position.x !== "number" || !Number.isFinite(position.x)
        || typeof position.y !== "number" || !Number.isFinite(position.y)) {
        return reject(`node ${id} has an invalid position`);
      }
    }
  }
  if (externalNodes > MAX_EXTERNAL_NODES) {
    return reject(`graph exceeds ${MAX_EXTERNAL_NODES} external nodes`);
  }

  const adjacency = new Map<string, string[]>();
  const indegree = new Map<string, number>();
  for (const id of ids) {
    adjacency.set(id, []);
    indegree.set(id, 0);
  }
  for (const rawEdge of graph.edges) {
    if (!isRecord(rawEdge) || typeof rawEdge.from !== "string" || typeof rawEdge.to !== "string") {
      return reject("every edge needs from and to ids");
    }
    if (!ids.has(rawEdge.from) || !ids.has(rawEdge.to)) return reject("edge references an unknown node");
    adjacency.get(rawEdge.from)?.push(rawEdge.to);
    indegree.set(rawEdge.to, (indegree.get(rawEdge.to) ?? 0) + 1);
  }

  const ready = [...ids].filter((id) => indegree.get(id) === 0).map((id) => ({ id, depth: 1 }));
  let visited = 0;
  let maxDepth = 0;
  while (ready.length > 0) {
    const current = ready.shift();
    if (!current) break;
    visited += 1;
    maxDepth = Math.max(maxDepth, current.depth);
    for (const next of adjacency.get(current.id) ?? []) {
      const remaining = (indegree.get(next) ?? 0) - 1;
      indegree.set(next, remaining);
      if (remaining === 0) ready.push({ id: next, depth: current.depth + 1 });
    }
  }
  if (visited !== ids.size) return reject("graph contains a cycle");
  if (maxDepth > MAX_DEPTH) return reject(`graph exceeds dependency depth ${MAX_DEPTH}`);
  return { ok: true };
}
