import type { Static } from "@sinclair/typebox";
import { resetDb, syncDb } from "@/lib/db";
import { dbExecute } from "@/lib/dbExecute";
import {
  createDbOperationIdentity,
  runWithDbOperation,
} from "@/lib/dbOperation";
import { withTimeout } from "@/lib/asyncTimeout";
import type { OrdersData } from "@tools/schemas/ib-orders";
import { filterExecutedToEtToday } from "@/lib/orders/executedToday";
import { isPriorSessionDayOrder } from "@/lib/orders/workingOrders";
import { readCachedOrdersSnapshot } from "@/lib/orders/ordersReadCache";

export type OrdersSnapshot = Static<typeof OrdersData>;

type Open = OrdersSnapshot["open_orders"][number];
type Executed = OrdersSnapshot["executed_orders"][number];

// SQL bound only: pull a short window then filter to ET calendar day in JS.
// 48h covers overnight + DST edges; "Today's Executed" is always ET day-cut.
const EXECUTED_SQL_LOOKBACK_HOURS = 48;
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
  // Pull the freshest cloud-DB state into the embedded replica before
  // reading. Without this we lag the disk JSON by up to 60s (the
  // background sync interval), which surfaces as transient `status`
  // drift on every order state transition (PreSubmitted → Submitted at
  // market open, Submitted → Filled, etc.).
  const syncIdentity = createDbOperationIdentity();
  syncIdentity.label = "orders replica sync";
  try {
    await withTimeout(
      runWithDbOperation(syncIdentity, syncDb),
      DB_READ_TIMEOUT_MS,
      `orders replica sync timed out after ${DB_READ_TIMEOUT_MS}ms`,
    );
  } catch {
    // Best-effort: a sync failure (network blip, auth hiccup) just means
    // we read the slightly-older replica — same as the pre-sync world.
    // Still drop the cached client: a hung sync is the same wedged-pool
    // signature the reads below would hit; let them rebuild fresh.
    resetDb(syncIdentity);
  }

  const cutoff = new Date(
    Date.now() - EXECUTED_SQL_LOOKBACK_HOURS * 60 * 60 * 1000,
  ).toISOString();
  const [openResult, execResult] = await Promise.all([
    dbExecute(
      {
        sql: `SELECT payload, updated_at FROM open_orders ORDER BY updated_at DESC`,
        args: [],
      },
      { timeoutMs: DB_READ_TIMEOUT_MS, label: "open orders" },
    ),
    dbExecute(
      {
        sql: `SELECT payload, fill_time FROM executed_orders
              WHERE fill_time >= ?
              ORDER BY fill_time DESC`,
        args: [cutoff],
      },
      { timeoutMs: DB_READ_TIMEOUT_MS, label: "executed orders" },
    ),
  ]);

  const open: Open[] = [];
  let latestOpenSync = "";
  for (const row of openResult.rows) {
    const payload = safeParse<Open>((row as { payload?: unknown }).payload);
    if (!payload) continue;
    const updatedAt = String((row as { updated_at?: unknown }).updated_at ?? "");
    if (isPriorSessionDayOrder(payload, updatedAt)) continue;
    open.push(payload);
    if (updatedAt > latestOpenSync) latestOpenSync = updatedAt;
  }

  const executedRaw: Executed[] = [];
  let latestExecSync = "";
  for (const row of execResult.rows) {
    const payload = safeParse<Executed>((row as { payload?: unknown }).payload);
    if (!payload) continue;
    executedRaw.push(payload);
    const fillTime = String((row as { fill_time?: unknown }).fill_time ?? "");
    if (fillTime > latestExecSync) latestExecSync = fillTime;
  }

  // Product contract: "Today's Executed" = America/New_York calendar day only.
  // SQL lookback is only a bound; never surface yesterday's 10:31 under "Today".
  const executed = filterExecutedToEtToday(executedRaw);

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
  return readCachedOrdersSnapshot(
    async () => (await readOrdersFromDb()) ?? EMPTY_ORDERS,
  );
}
