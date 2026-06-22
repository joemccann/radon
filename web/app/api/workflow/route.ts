import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { getDb } from "@/lib/db";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// F14 — workflow graph persistence. AUTH-REQUIRED, user-scoped (mirrors the F6
// alert_rules CRUD pattern). Graphs are {nodes, edges} JSON saved to Turso
// (migration 0019). The Python side reads the same table for server-side runs.

type WorkflowRow = {
  id: string;
  name: string;
  graph: string;
  updated_at: string;
  last_run_ok: number | null;
  last_run_at: string | null;
};

function unauthorized(requestId: string): Response {
  return setNoStoreResponseHeaders(
    jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
    requestId,
  );
}

function validationError(requestId: string, message: string): Response {
  return setNoStoreResponseHeaders(
    jsonApiError({ status: 400, code: "VALIDATION_ERROR", message, requestId }),
    requestId,
  );
}

function parseGraph(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return { nodes: [], edges: [] };
  }
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) return unauthorized(requestId);

  try {
    const db = getDb();
    const result = await db.execute({
      sql: `SELECT id, name, graph, updated_at, last_run_ok, last_run_at
            FROM workflow_graphs
            WHERE user_id = ?
            ORDER BY updated_at DESC`,
      args: [userId],
    });
    const graphs = result.rows.map((r) => {
      const row = r as unknown as WorkflowRow;
      return {
        id: row.id,
        name: row.name,
        graph: parseGraph(row.graph),
        updated_at: row.updated_at,
        last_run_ok: row.last_run_ok,
        last_run_at: row.last_run_at,
      };
    });
    return setNoStoreResponseHeaders(NextResponse.json({ graphs }), requestId);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 500, code: "INTERNAL_ERROR", message: "Failed to read workflows", detail: message, requestId }),
      requestId,
    );
  }
}

export async function POST(req: Request): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) return unauthorized(requestId);

  let body: { id?: unknown; name?: unknown; graph?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return validationError(requestId, "Invalid JSON body");
  }

  if (typeof body.name !== "string" || body.name.trim().length === 0) {
    return validationError(requestId, "name is required");
  }
  if (typeof body.graph !== "object" || body.graph === null) {
    return validationError(requestId, "graph is required");
  }
  const graph = body.graph as { nodes?: unknown; edges?: unknown };
  if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    return validationError(requestId, "graph must have nodes[] and edges[]");
  }

  const id = typeof body.id === "string" && body.id.length > 0 ? body.id : crypto.randomUUID();
  const name = body.name.trim();
  const graphJson = JSON.stringify(body.graph);

  try {
    const db = getDb();
    await db.execute({
      sql: `INSERT INTO workflow_graphs (id, user_id, name, graph, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              graph = excluded.graph,
              updated_at = excluded.updated_at`,
      args: [id, userId, name, graphJson],
    });
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true, id }), requestId);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 500, code: "INTERNAL_ERROR", message: "Failed to save workflow", detail: message, requestId }),
      requestId,
    );
  }
}
