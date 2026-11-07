import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import {
  QUANT_MISSING,
  isQuantStrategyKey,
  quantLabel,
  type QuantData,
  type QuantOrderRow,
  type QuantRun,
  type QuantTargetRow,
} from "@/lib/quant";
import { getFreshnessWindowMs } from "@/lib/serviceHealthWindows";

/**
 * GET /api/quant/[strategy]
 *
 * Quant sleeve read (docs/quant-strategies.md): the latest backtest run,
 * the latest target weights and the latest execution plan for one registered
 * strategy, straight from the Turso tables scripts/quant_run.py and
 * scripts/quant_execute.py write. 200 + `missing: true` when nothing has
 * been persisted yet; 400 only for a key the registry does not know.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

// The targets writer is radon-quant-targets.timer (weekdays after the close);
// its closed-market window is the budget a read is "fresh" within. A backtest
// run is a one-off and is served whatever its age.
const QUANT_MAX_AGE_MS = getFreshnessWindowMs("quant-targets", "closed");

type Row = Record<string, unknown>;

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "bigint") return Number(v);
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function latestMs(...stamps: Array<string | null>): number | null {
  let best: number | null = null;
  for (const stamp of stamps) {
    const ms = contentTimestampMs(stamp);
    if (ms != null && (best == null || ms > best)) best = ms;
  }
  return best;
}

function parseRun(row: Row | undefined): QuantRun | null {
  if (!row) return null;
  let payload: Row = {};
  try {
    payload = JSON.parse(String(row.payload ?? "{}")) as Row;
  } catch {
    payload = {};
  }
  const metrics = (payload.metrics ?? {}) as Row;
  const dates = Array.isArray(payload.dates) ? (payload.dates as string[]) : [];
  const equity = Array.isArray(payload.equity) ? (payload.equity as number[]) : [];
  return {
    run_at: String(row.run_at),
    since: str(row.since) ?? str(payload.since),
    until: str(row.until) ?? str(payload.until),
    n_sessions: num(row.n_sessions) ?? dates.length,
    n_rebalances: num(row.n_rebalances) ?? 0,
    cost_bps: num(payload.cost_bps),
    universe: Array.isArray(payload.universe) ? (payload.universe as string[]) : [],
    metrics: {
      sharpe: num(metrics.sharpe),
      sortino: num(metrics.sortino),
      calmar: num(metrics.calmar),
      max_drawdown: num(metrics.max_drawdown),
      hit_rate: num(metrics.hit_rate),
      expectancy: num(metrics.expectancy),
      annualized_return: num(metrics.annualized_return),
      ann_vol: num(metrics.ann_vol),
      total_return: num(metrics.total_return),
      avg_turnover: num(metrics.avg_turnover),
      cost_drag: num(metrics.cost_drag),
      mean_gross: num(metrics.mean_gross),
      mean_net: num(metrics.mean_net),
    },
    final_weights: (payload.final_weights ?? {}) as Record<string, number>,
    dates,
    equity,
  };
}

async function readTargets(db: ReturnType<typeof getDb>, strategy: string): Promise<Row[]> {
  const where = `WHERE strategy = ? AND as_of = (SELECT MAX(as_of) FROM quant_targets WHERE strategy = ?)`;
  try {
    const res = await db.execute({
      sql: `SELECT as_of, symbol, weight, notional, recorded_at, close, rebalance_today
            FROM quant_targets ${where} ORDER BY symbol`,
      args: [strategy, strategy],
    });
    return res.rows as unknown as Row[];
  } catch {
    // Pre-0109 schema: no close / rebalance_today columns yet.
    const res = await db.execute({
      sql: `SELECT as_of, symbol, weight, notional, recorded_at FROM quant_targets ${where} ORDER BY symbol`,
      args: [strategy, strategy],
    });
    return res.rows as unknown as Row[];
  }
}

async function readOrders(db: ReturnType<typeof getDb>, strategy: string): Promise<Row[]> {
  try {
    const res = await db.execute({
      sql: `SELECT as_of, symbol, action, quantity, limit_price, status, message, placed_at
            FROM quant_orders
            WHERE strategy = ? AND as_of = (SELECT MAX(as_of) FROM quant_orders WHERE strategy = ?)
            ORDER BY placed_at DESC, symbol`,
      args: [strategy, strategy],
    });
    return res.rows as unknown as Row[];
  } catch {
    // The execution bridge's table lands with migration 0109; a run without it
    // still serves the backtest and the targets.
    return [];
  }
}

async function readQuantFromDb(strategy: string): Promise<TimestampedRead<QuantData> | null> {
  const db = getDb();
  const runRes = await db.execute({
    sql: `SELECT run_at, since, until, n_sessions, n_rebalances, payload
          FROM quant_runs WHERE strategy = ? ORDER BY run_at DESC LIMIT 1`,
    args: [strategy],
  });
  const run = parseRun(runRes.rows[0] as unknown as Row | undefined);
  const targetRows = await readTargets(db, strategy);
  const orderRows = await readOrders(db, strategy);
  if (!run && targetRows.length === 0 && orderRows.length === 0) return null;

  const targets = targetRows.length
    ? {
        as_of: String(targetRows[0].as_of),
        recorded_at: str(targetRows[0].recorded_at),
        rebalance_today: (num(targetRows[0].rebalance_today) ?? 0) !== 0,
        rows: targetRows.map<QuantTargetRow>((r) => ({
          symbol: String(r.symbol),
          weight: num(r.weight) ?? 0,
          notional: num(r.notional),
          close: num(r.close),
        })),
      }
    : null;

  const orders = orderRows.length
    ? {
        as_of: String(orderRows[0].as_of),
        placed_at: str(orderRows[0].placed_at),
        rows: orderRows.map<QuantOrderRow>((r) => ({
          symbol: String(r.symbol),
          action: String(r.action ?? ""),
          quantity: num(r.quantity) ?? 0,
          limit_price: num(r.limit_price),
          status: String(r.status ?? ""),
          message: str(r.message),
        })),
      }
    : null;

  return {
    data: { strategy, label: quantLabel(strategy), missing: false, run, targets, orders },
    timestampMs: latestMs(run?.run_at ?? null, targets?.recorded_at ?? null, orders?.placed_at ?? null),
  };
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ strategy: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  const { strategy } = await context.params;
  if (!isQuantStrategyKey(strategy)) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ status: "unknown_strategy", strategy }, { status: 400 }),
      requestId,
    );
  }
  const result = await dbFirstRead({
    fromDb: () => readQuantFromDb(strategy),
    maxAgeMs: QUANT_MAX_AGE_MS,
    label: `quant:${strategy}`,
  });
  const response = NextResponse.json(result.ok ? result.data : QUANT_MISSING(strategy));
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["quant"],
  });
}
