import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { validateWorkflowGraph } from "@/lib/workflow/validateGraph";

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

export const radonCapability = { GET: "read", POST: "mutate.workspace" };

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) return unauthorized(requestId);

  try {
    const result = await dbExecute(
      {
        sql: `SELECT id, name, graph, updated_at, last_run_ok, last_run_at
            FROM workflow_graphs
            WHERE user_id = ?
            ORDER BY updated_at DESC`,
        args: [userId],
      },
      { label: "workflow" },
    );
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
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Workflow store temporarily unavailable", detail: describeDbError(err), requestId }),
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

  if (typeof body.name !== "string" || body.name.trim().length === 0 || body.name.trim().length > 100) {
    return validationError(requestId, "name is required");
  }
  const validation = validateWorkflowGraph(body.graph);
  if (!validation.ok) return validationError(requestId, validation.message);

  const id = typeof body.id === "string" && body.id.length > 0 ? body.id : crypto.randomUUID();
  if (!/^[0-9a-f-]{36}$/i.test(id)) return validationError(requestId, "id is invalid");
  const name = body.name.trim();
  const graphJson = JSON.stringify(body.graph);

  try {
    if (body.id === undefined) {
      await dbExecute(
        {
          sql: `INSERT INTO workflow_graphs (id, user_id, name, graph, created_at, updated_at)
              VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))`,
          args: [id, userId, name, graphJson],
        },
        { label: "workflow" },
      );
    } else {
      const result = await dbExecute(
        {
          sql: `UPDATE workflow_graphs
              SET name = ?, graph = ?, updated_at = datetime('now')
              WHERE id = ? AND user_id = ?`,
          args: [name, graphJson, id, userId],
        },
        { label: "workflow" },
      );
      if (result.rowsAffected === 0) {
        return setNoStoreResponseHeaders(
          jsonApiError({ status: 404, code: "NOT_FOUND", message: "Workflow not found", requestId }),
          requestId,
        );
      }
    }
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true, id }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Workflow store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}
