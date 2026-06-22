import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { getDb } from "@/lib/db";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const VALID_OPS = new Set([">", "<", ">=", "<="]);
const VALID_CHANNELS = new Set(["pushover", "service_health"]);

type AlertRuleRow = {
  id: string;
  ticker: string;
  metric: string;
  op: string;
  threshold: number;
  channel: string;
  created_at: string;
  last_fired_at: string | null;
};

function rowToRule(row: AlertRuleRow) {
  return {
    id: row.id,
    ticker: row.ticker,
    metric: row.metric,
    op: row.op,
    threshold: row.threshold,
    channel: row.channel,
    created_at: row.created_at,
    last_fired_at: row.last_fired_at,
  };
}

function unauthorized(requestId: string): Response {
  return setNoStoreResponseHeaders(
    jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
    requestId,
  );
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) return unauthorized(requestId);

  try {
    const db = getDb();
    const result = await db.execute({
      sql: `SELECT id, ticker, metric, op, threshold, channel, created_at, last_fired_at
            FROM alert_rules
            WHERE user_id = ?
            ORDER BY created_at DESC`,
      args: [userId],
    });
    const rules = result.rows.map((r) => rowToRule(r as unknown as AlertRuleRow));
    return setNoStoreResponseHeaders(NextResponse.json({ rules }), requestId);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 500, code: "INTERNAL_ERROR", message: "Failed to read alert rules", detail: message, requestId }),
      requestId,
    );
  }
}

function validationError(requestId: string, message: string): Response {
  return setNoStoreResponseHeaders(
    jsonApiError({ status: 400, code: "VALIDATION_ERROR", message, requestId }),
    requestId,
  );
}

export async function POST(req: Request): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) return unauthorized(requestId);

  let body: { ticker?: unknown; metric?: unknown; op?: unknown; threshold?: unknown; channel?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "BAD_REQUEST", message: "Invalid JSON body", requestId }),
      requestId,
    );
  }

  if (typeof body.ticker !== "string" || body.ticker.trim().length === 0) {
    return validationError(requestId, "ticker is required");
  }
  if (typeof body.metric !== "string" || body.metric.trim().length === 0) {
    return validationError(requestId, "metric is required");
  }
  if (typeof body.op !== "string" || !VALID_OPS.has(body.op)) {
    return validationError(requestId, "op must be one of >, <, >=, <=");
  }
  const threshold = typeof body.threshold === "number" ? body.threshold : Number(body.threshold);
  if (!Number.isFinite(threshold)) {
    return validationError(requestId, "threshold must be a number");
  }
  const channel = typeof body.channel === "string" && VALID_CHANNELS.has(body.channel) ? body.channel : "pushover";

  const ticker = body.ticker.trim().toUpperCase();
  const metric = body.metric.trim();

  try {
    const db = getDb();
    await db.execute({
      sql: `INSERT INTO alert_rules (id, user_id, ticker, metric, op, threshold, channel, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`,
      args: [crypto.randomUUID(), userId, ticker, metric, body.op, threshold, channel],
    });
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true }), requestId);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 500, code: "INTERNAL_ERROR", message: "Failed to create alert rule", detail: message, requestId }),
      requestId,
    );
  }
}
