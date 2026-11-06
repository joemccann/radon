import { describe, expect, it } from "vitest";

import {
  ALL_STATUSES,
  CANCEL_OUTCOMES,
  FILLED,
  IB_ORDER_STATUS,
  TERMINAL,
  TERMINAL_FAILED,
  UNACKNOWLEDGED,
  WORKING,
  isTerminal,
  isWorking,
  normalizeIbOrderStatus,
} from "@/lib/orders/ibOrderStatus";

describe("ibOrderStatus vocabulary", () => {
  it("exposes the exact IB strings", () => {
    expect(IB_ORDER_STATUS).toEqual({
      PENDING_SUBMIT: "PendingSubmit",
      PENDING_CANCEL: "PendingCancel",
      PRE_SUBMITTED: "PreSubmitted",
      SUBMITTED: "Submitted",
      API_PENDING: "ApiPending",
      API_CANCELLED: "ApiCancelled",
      CANCELLED: "Cancelled",
      FILLED: "Filled",
      INACTIVE: "Inactive",
      REJECTED: "Rejected",
      UNKNOWN: "Unknown",
    });
  });

  it("partitions statuses into the canonical sets", () => {
    expect([...WORKING].sort()).toEqual(["ApiPending", "PendingCancel", "PendingSubmit", "PreSubmitted", "Submitted"]);
    expect([...TERMINAL_FAILED].sort()).toEqual(["ApiCancelled", "Cancelled", "Inactive", "Rejected"]);
    expect([...FILLED]).toEqual(["Filled"]);
    expect([...TERMINAL].sort()).toEqual([...TERMINAL_FAILED, ...FILLED].sort());
    expect([...UNACKNOWLEDGED].sort()).toEqual(["ApiPending", "PendingSubmit", "Unknown"]);
    expect([...WORKING].some((s) => TERMINAL.has(s))).toBe(false);
    expect([...ALL_STATUSES].sort()).toEqual([...WORKING, ...TERMINAL, "Unknown"].sort());
  });

  it.each(["Submitted", "PreSubmitted", "PendingSubmit", "ApiPending", "PendingCancel"])("%s is working", (s) => {
    expect(isWorking(s)).toBe(true);
    expect(isTerminal(s)).toBe(false);
  });

  it.each(["Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"])("%s is terminal", (s) => {
    expect(isTerminal(s)).toBe(true);
    expect(isWorking(s)).toBe(false);
  });

  it.each([null, undefined, "", "Unknown", "submitted", "garbage"])("%s is neither (strict)", (s) => {
    expect(isWorking(s)).toBe(false);
    expect(isTerminal(s)).toBe(false);
  });

  it.each([
    ["Submitted", "Submitted"],
    ["submitted", "Submitted"],
    ["  PRESUBMITTED ", "PreSubmitted"],
    ["apicancelled", "ApiCancelled"],
    ["unknown", "Unknown"],
    ["rejected", "Rejected"],
    ["", null],
    [null, null],
    [undefined, null],
    ["Working", null],
    [123, null],
  ] as const)("normalize(%s) -> %s", (raw, expected) => {
    expect(normalizeIbOrderStatus(raw)).toBe(expected);
  });

  it("lists cancel outcomes", () => {
    expect(CANCEL_OUTCOMES).toEqual([
      "cancelled",
      "filled",
      "partially_filled_then_cancelled",
      "still_working",
      "unknown",
    ]);
  });
});
