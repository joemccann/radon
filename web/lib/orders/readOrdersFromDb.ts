import type { Static } from "@sinclair/typebox";
import { getDb, syncDb } from "@/lib/db";
import { withTimeout } from "@/lib/asyncTimeout";
import type { OrdersData } from "@tools/schemas/ib-orders";

export type OrdersSnapshot = Static<typeof OrdersData>;

type Open = OrdersSnapshot["open_orders"][number];
type Executed = OrdersSnapshot["executed_orders"][number];

const EXECUTED_LOOKBACK_HOURS = 36;
const DB_READ_TIMEOUT_MS = 3_000;

export const EMPTY_ORDERS: OrdersSnapshot = {
  last_sync: "",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function safeParse<T>(text: unknown): T | null {
  if (typeof text !== "string" || !text) return null;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

export async function readOrdersFromDb(): Promise<OrdersSnapshot | null> {
  const db = getDb();

  // Pull the freshest cloud-DB state into the embedded replica before
  // reading. Without this we lag the disk JSON by up to 60s (the
  // background sync interval), which surfaces as transient `status`
  // drift on every order state transition (PreSubmitted → Submitted at
  // market open, Submitted → Filled, etc.).
  try {
    await withTimeout(
      syncDb(),
      DB_READ_TIMEOUT_MS,
      `orders replica sync timed out after ${DB_READ_TIMEOUT_MS}ms`,
    );
  } catch {
    // Best-effort: a sync failure (network blip, auth hiccup) just means
    // we read the slightly-older replica — same as the pre-sync world.
  }

  const openResult = await withTimeout(
    db.execute({
      sql: `SELECT payload, updated_at FROM open_orders ORDER BY updated_at DESC`,
      args: [],
    }),
    DB_READ_TIMEOUT_MS,
    `open orders read timed out after ${DB_READ_TIMEOUT_MS}ms`,
  );

  const cutoff = new Date(Date.now() - EXECUTED_LOOKBACK_HOURS * 60 * 60 * 1000).toISOString();
  const execResult = await withTimeout(
    db.execute({
      sql: `SELECT payload, fill_time FROM executed_orders
            WHERE fill_time >= ?
            ORDER BY fill_time DESC`,
      args: [cutoff],
    }),
    DB_READ_TIMEOUT_MS,
    `executed orders read timed out after ${DB_READ_TIMEOUT_MS}ms`,
  );

  const open: Open[] = [];
  let latestOpenSync = "";
  for (const row of openResult.rows) {
    const payload = safeParse<Open>((row as { payload?: unknown }).payload);
    if (!payload) continue;
    open.push(payload);
    const updatedAt = String((row as { updated_at?: unknown }).updated_at ?? "");
    if (updatedAt > latestOpenSync) latestOpenSync = updatedAt;
  }

  const executed: Executed[] = [];
  let latestExecSync = "";
  for (const row of execResult.rows) {
    const payload = safeParse<Executed>((row as { payload?: unknown }).payload);
    if (!payload) continue;
    executed.push(payload);
    const fillTime = String((row as { fill_time?: unknown }).fill_time ?? "");
    if (fillTime > latestExecSync) latestExecSync = fillTime;
  }

  if (open.length === 0 && executed.length === 0) return null;

  return {
    last_sync: latestOpenSync || latestExecSync || "",
    open_orders: open,
    executed_orders: executed,
    open_count: open.length,
    executed_count: executed.length,
  };
}

export async function readOrdersSnapshotFromDb(): Promise<OrdersSnapshot> {
  return (await readOrdersFromDb()) ?? EMPTY_ORDERS;
}
