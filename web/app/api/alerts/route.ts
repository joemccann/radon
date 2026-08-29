import { NextResponse } from "next/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { boundedTicker } from "@/lib/requestBounds";
import { requireRouteAccess } from "@/lib/routeAccess";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const VALID_OPS = new Set([">", "<", ">=", "<="]);
const VALID_CHANNELS = new Set(["pushover", "service_health"]);
const METRIC_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const MAX_RULES_PER_USER = 50;
// Server-side mirror of the UI's metric-aware threshold bounds: a buy_ratio rule
// with a 0-100 threshold can never fire. Unknown metrics skip the range check so
// the metric set can grow without a server change.
const METRIC_RANGES: Record<string, [number, number]> = {
  flow_strength: [0, 100],
  score: [0, 100],
  buy_ratio: [0, 1],
};

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

export const radonCapability = { GET: "read", POST: "mutate.workspace" };

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const access = await requireRouteAccess(undefined, {
    rate: { key: "alerts-read", limit: 120, windowMs: 60_000 },
    durableRateTier: "A",
  });
  if (!access.ok) return access.response;

  try {
    const result = await dbExecute(
      {
        sql: `SELECT id, ticker, metric, op, threshold, channel, created_at, last_fired_at
            FROM alert_rules
            WHERE user_id = ?
            ORDER BY created_at DESC`,
        args: [access.principal.userId],
      },
      { label: "alerts" },
    );
    const rules = result.rows.map((r) => rowToRule(r as unknown as AlertRuleRow));
    return setNoStoreResponseHeaders(NextResponse.json({ rules }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Alert store temporarily unavailable", detail: describeDbError(err), requestId }),
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
  const access = await requireRouteAccess(req, {
    rate: { key: "alerts-create", limit: 20, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;

  let body: { ticker?: unknown; metric?: unknown; op?: unknown; threshold?: unknown; channel?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "BAD_REQUEST", message: "Invalid JSON body", requestId }),
      requestId,
    );
  }

  const ticker = boundedTicker(body.ticker);
  if (!ticker) return validationError(requestId, "ticker is invalid");
  if (typeof body.metric !== "string" || !METRIC_PATTERN.test(body.metric.trim())) {
    return validationError(requestId, "metric is invalid");
  }
  if (typeof body.op !== "string" || !VALID_OPS.has(body.op)) {
    return validationError(requestId, "op must be one of >, <, >=, <=");
  }
  const threshold = typeof body.threshold === "number" ? body.threshold : Number(body.threshold);
  if (!Number.isFinite(threshold)) {
    return validationError(requestId, "threshold must be a number");
  }
  const channel = typeof body.channel === "string" && VALID_CHANNELS.has(body.channel) ? body.channel : "pushover";

  const metric = body.metric.trim();

  const range = METRIC_RANGES[metric];
  if (range && (threshold < range[0] || threshold > range[1])) {
    return validationError(requestId, `threshold for ${metric} must be between ${range[0]} and ${range[1]}`);
  }

  try {
    const result = await dbExecute(
      {
        sql: `INSERT INTO alert_rules (id, user_id, ticker, metric, op, threshold, channel, created_at)
            SELECT ?, ?, ?, ?, ?, ?, ?, datetime('now')
            WHERE (SELECT COUNT(*) FROM alert_rules WHERE user_id = ?) < ?
              AND NOT EXISTS (
                SELECT 1 FROM alert_rules
                WHERE user_id = ? AND ticker = ? AND metric = ? AND op = ?
                  AND threshold = ? AND channel = ?
              )`,
        args: [
          crypto.randomUUID(), access.principal.userId, ticker, metric, body.op, threshold, channel,
          access.principal.userId, MAX_RULES_PER_USER,
          access.principal.userId, ticker, metric, body.op, threshold, channel,
        ],
      },
      { label: "alerts" },
    );
    if (result.rowsAffected === 0) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          status: 409,
          code: "CONFLICT",
          message: `Alert rule already exists or the ${MAX_RULES_PER_USER}-rule limit was reached`,
          requestId,
        }),
        requestId,
      );
    }
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Alert store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}
