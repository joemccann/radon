/**
 * Canonical IB order-status vocabulary. Mirrors
 * `scripts/utils/ib_order_status.py`; `scripts/tests/test_ib_order_status_parity.py`
 * parses this file and fails if the two drift, so keep the
 * `export type X = "a" | "b";` and `new Set<T>([...])` shapes.
 */

export type IbOrderStatus = "PendingSubmit" | "PendingCancel" | "PreSubmitted" | "Submitted" | "ApiPending" | "ApiCancelled" | "Cancelled" | "Filled" | "Inactive" | "Rejected" | "Unknown";

export const IB_ORDER_STATUS = {
  PENDING_SUBMIT: "PendingSubmit",
  PENDING_CANCEL: "PendingCancel",
  PRE_SUBMITTED: "PreSubmitted",
  SUBMITTED: "Submitted",
  API_PENDING: "ApiPending",
  API_CANCELLED: "ApiCancelled",
  CANCELLED: "Cancelled",
  FILLED: "Filled",
  INACTIVE: "Inactive",
  /** Not an ib_insync OrderStatus; local callers treat it as a terminal failure. */
  REJECTED: "Rejected",
  /** Local placeholder before IB reports any status. */
  UNKNOWN: "Unknown",
} as const satisfies Record<string, IbOrderStatus>;

/** Still live at the broker (a PendingCancel order can still fill). */
export const WORKING: ReadonlySet<IbOrderStatus> = new Set<IbOrderStatus>(["PendingSubmit", "ApiPending", "PreSubmitted", "Submitted", "PendingCancel"]);
export const TERMINAL_FAILED: ReadonlySet<IbOrderStatus> = new Set<IbOrderStatus>(["Cancelled", "ApiCancelled", "Inactive", "Rejected"]);
export const FILLED: ReadonlySet<IbOrderStatus> = new Set<IbOrderStatus>(["Filled"]);
export const TERMINAL: ReadonlySet<IbOrderStatus> = new Set<IbOrderStatus>(["Cancelled", "ApiCancelled", "Inactive", "Rejected", "Filled"]);
/** No broker acknowledgement yet. */
export const UNACKNOWLEDGED: ReadonlySet<IbOrderStatus> = new Set<IbOrderStatus>(["PendingSubmit", "ApiPending", "Unknown"]);
export const ALL_STATUSES: ReadonlySet<IbOrderStatus> = new Set<IbOrderStatus>(Object.values(IB_ORDER_STATUS));

export type CancelOutcome = "cancelled" | "filled" | "partially_filled_then_cancelled" | "still_working" | "unknown";

export const CANCEL_OUTCOMES: readonly CancelOutcome[] = [
  "cancelled",
  "filled",
  "partially_filled_then_cancelled",
  "still_working",
  "unknown",
];

const BY_LOWER: ReadonlyMap<string, IbOrderStatus> = new Map(
  [...ALL_STATUSES].map((s) => [s.toLowerCase(), s]),
);

/** Case-insensitive match to a canonical status, else null. */
export function normalizeIbOrderStatus(status: unknown): IbOrderStatus | null {
  if (typeof status !== "string") return null;
  return BY_LOWER.get(status.trim().toLowerCase()) ?? null;
}

/** True only for an exact canonical working status. */
export function isWorking(status: unknown): status is IbOrderStatus {
  return typeof status === "string" && WORKING.has(status as IbOrderStatus);
}

/** True only for an exact canonical terminal status (filled or failed). */
export function isTerminal(status: unknown): status is IbOrderStatus {
  return typeof status === "string" && TERMINAL.has(status as IbOrderStatus);
}
