import type { ExecutedOrder } from "./types";

/**
 * Persistent fill-toast logic (pure, node-testable).
 *
 * Detects new execution rows in the `useOrders` poll and formats the toast
 * copy. The stateful side (refs, sessionStorage, addToast) lives in
 * `useFillToasts.ts`; everything here is side-effect free.
 */

export const SEEN_STORAGE_KEY = "radon.fill-toast-seen";
export const MAX_SEEN_KEYS = 300;

/** Minimal storage surface so tests don't need a real sessionStorage. */
export type SeenStorage = Pick<Storage, "getItem" | "setItem">;

/**
 * Stable per-execution dedupe key. IB reissues corrected executions with a
 * suffixed execId (`.01` → `.02`); the trailing dot-segment is the correction
 * counter, so stripping it collapses corrections onto one key. Null when the
 * row has no usable execId (payloads parse unvalidated from Turso).
 */
export function execKey(fill: ExecutedOrder): string | null {
  const execId = fill.execId;
  if (typeof execId !== "string" || execId === "") return null;
  return execId.replace(/\.\d+$/, "");
}

/** Baseline key set from the first payload after mount — never toastable. */
export function primeSeen(executed: ExecutedOrder[]): Set<string> {
  const seen = new Set<string>();
  for (const fill of executed) {
    const key = execKey(fill);
    if (key) seen.add(key);
  }
  return seen;
}

/**
 * Unseen fills in feed order. Pure — does not mutate `seen`. A correction
 * pair arriving in the same payload yields only its first row.
 */
export function diffNewFills(seen: Set<string>, executed: ExecutedOrder[]): ExecutedOrder[] {
  const fresh: ExecutedOrder[] = [];
  const emitted = new Set<string>();
  for (const fill of executed) {
    const key = execKey(fill);
    if (!key || seen.has(key) || emitted.has(key)) continue;
    emitted.add(key);
    fresh.push(fill);
  }
  return fresh;
}

const SIDE_LABELS: Record<string, string> = { BOT: "BUY", SLD: "SELL" };

function instrumentLabel(fill: ExecutedOrder): string {
  const contract = fill.contract;
  const symbol = fill.symbol || contract?.symbol || "?";
  if (contract?.secType === "OPT" && contract.strike != null) {
    const right = contract.right === "P" ? "P" : "C";
    return `${symbol} $${contract.strike}${right}`;
  }
  return symbol;
}

/**
 * `FILLED · SELL 25x EWY $175C @ $4.55` — terse terminal voice, no em dashes.
 * Rows parse unvalidated from Turso: a malformed side/quantity drops its
 * clause instead of rendering "undefined nullx".
 */
export function formatFillToast(fill: ExecutedOrder): string {
  const rawSide = typeof fill.side === "string" ? fill.side : "";
  const side = SIDE_LABELS[rawSide] ?? rawSide;
  const qty =
    typeof fill.quantity === "number" && Number.isFinite(fill.quantity)
      ? `${fill.quantity}x `
      : "";
  const price = fill.avgPrice != null ? ` @ $${fill.avgPrice.toFixed(2)}` : "";
  const sidePart = side ? `${side} ` : "";
  return `FILLED · ${sidePart}${qty}${instrumentLabel(fill)}${price}`;
}

/**
 * True when a prior mount in this browser session already primed and saved a
 * baseline (the KEY's presence is the marker — an empty array still counts).
 * Distinguishes "fresh session, prime silently" from "remount, diff against
 * the restored baseline".
 */
export function hasSeenBaseline(storage: SeenStorage): boolean {
  try {
    const raw = storage.getItem(SEEN_STORAGE_KEY);
    if (!raw) return false;
    return Array.isArray(JSON.parse(raw) as unknown);
  } catch {
    return false;
  }
}

/** Load the persisted seen set; corrupt or non-array payloads yield empty. */
export function loadSeen(storage: SeenStorage): Set<string> {
  try {
    const raw = storage.getItem(SEEN_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((k): k is string => typeof k === "string"));
  } catch {
    return new Set();
  }
}

/** Persist the seen set, bounded to the newest MAX_SEEN_KEYS entries. */
export function saveSeen(storage: SeenStorage, seen: Set<string>): void {
  try {
    const keys = Array.from(seen).slice(-MAX_SEEN_KEYS);
    storage.setItem(SEEN_STORAGE_KEY, JSON.stringify(keys));
  } catch {
    // Storage full/unavailable — dedupe degrades to in-memory only.
  }
}
