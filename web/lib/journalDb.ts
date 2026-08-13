import { getDb } from "@/lib/db";
import {
  syncNewTrades,
  type ReconciliationTrade,
  type SyncResult,
} from "@/lib/journalImport";

export type JournalPayload = {
  trades: unknown[];
  error?: string;
};

type ReconciliationPayload = {
  snapshot_at?: string;
  timestamp?: string;
  new_trades?: unknown[];
  needs_attention?: boolean;
};

function safeParseRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "string" || value.length === 0) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function filledAtFor(trade: Record<string, unknown>): string | null {
  for (const key of ["filled_at", "date", "close_date"]) {
    const value = trade[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

function tradeIdFor(trade: Record<string, unknown>): string {
  const execId = trade.ib_exec_id;
  if (typeof execId === "string" && execId.length > 0) return execId;

  const id = trade.id;
  if (typeof id === "number" && Number.isFinite(id)) return `manual-${id}`;
  if (typeof id === "string" && id.length > 0) return `manual-${id}`;

  const ticker = String(trade.ticker ?? trade.symbol ?? "unknown");
  const date = String(trade.date ?? trade.filled_at ?? trade.close_date ?? "");
  const action = String(trade.action ?? trade.decision ?? "");
  const qty = String(trade.contracts ?? trade.shares ?? trade.quantity ?? "");
  return `${ticker}|${date}|${action}|${qty}`;
}

function normalizeExistingTrades(trades: unknown[]): Array<Record<string, unknown>> {
  return trades
    .filter((trade): trade is Record<string, unknown> =>
      trade !== null && typeof trade === "object" && !Array.isArray(trade),
    )
    .map((trade, index) => ({
      ...trade,
      id: typeof trade.id === "number" && Number.isFinite(trade.id) ? trade.id : index + 1,
    }));
}

export async function readJournalFromDb(limit = 5000): Promise<JournalPayload> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT payload FROM journal ORDER BY filled_at DESC, written_at DESC LIMIT ?`,
    args: [limit],
  });
  const trades: unknown[] = [];
  for (const row of result.rows) {
    const payload = safeParseRecord((row as unknown as { payload?: unknown }).payload);
    if (payload) trades.push(payload);
  }
  return { trades };
}

export async function readReconciliationFromDb(snapshotAt: string): Promise<ReconciliationPayload | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT payload FROM reconciliation_log WHERE snapshot_at = ? LIMIT 1`,
    args: [snapshotAt],
  });
  if (result.rows.length === 0) return null;
  const payload = safeParseRecord((result.rows[0] as unknown as { payload?: unknown }).payload);
  return payload as ReconciliationPayload | null;
}

export async function readLatestReconciliationFromDb(): Promise<ReconciliationPayload | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT payload FROM reconciliation_log ORDER BY snapshot_at DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const payload = safeParseRecord((result.rows[0] as unknown as { payload?: unknown }).payload);
  return payload as ReconciliationPayload | null;
}

async function importReconciliationToJournal(
  reconciliation: ReconciliationPayload | null,
): Promise<SyncResult> {
  const newTrades = Array.isArray(reconciliation?.new_trades)
    ? reconciliation.new_trades as ReconciliationTrade[]
    : [];
  if (newTrades.length === 0) {
    return { imported: 0, skipped: 0, trades: [] };
  }

  const existing = await readJournalFromDb();
  const result = syncNewTrades(
    normalizeExistingTrades(existing.trades) as Parameters<typeof syncNewTrades>[0],
    newTrades,
  );
  if (result.needs_rehydration) {
    throw new Error("Authoritative execution rehydration required");
  }
  if (result.trades.length === 0) return result;

  const db = getDb();
  const writtenAt = new Date().toISOString();
  for (const trade of result.trades) {
    const payload = trade as Record<string, unknown>;
    const isCorrection = typeof payload.ib_exec_id_corrected === "string";
    await db.execute({
      sql: `
        INSERT INTO journal (trade_id, payload, filled_at, written_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(trade_id) DO ${isCorrection ? `UPDATE SET
          payload = excluded.payload,
          filled_at = excluded.filled_at,
          written_at = excluded.written_at` : "NOTHING"}
      `,
      args: [
        tradeIdFor(payload),
        JSON.stringify(payload),
        filledAtFor(payload),
        writtenAt,
      ],
    });
  }

  return result;
}

export async function importLatestReconciliationToJournal(): Promise<SyncResult> {
  return importReconciliationToJournal(await readLatestReconciliationFromDb());
}

export async function importReconciliationSnapshotToJournal(snapshotAt: string): Promise<SyncResult> {
  if (!snapshotAt) throw new Error("Exact reconciliation snapshot is required");
  const reconciliation = await readReconciliationFromDb(snapshotAt);
  if (!reconciliation) throw new Error("Returned reconciliation snapshot is unavailable");
  return importReconciliationToJournal(reconciliation);
}
