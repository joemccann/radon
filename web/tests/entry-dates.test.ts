/**
 * Per-contract entry-date derivation from journal rows.
 *
 * `buildContractEntryDates` walks each contract's fills in date order using
 * signed net quantity from the action label (mirrors
 * scripts/clients/journal_basis.py:_signed_qty) and records the date net
 * quantity last departed zero — the opening date of the LAST open episode.
 * Journal `filled_at`/`$.date` are date-only, so values stay date-only.
 */
import { describe, it, expect } from "vitest";
import { buildContractEntryDates, contractEntryKey } from "../lib/entryDates";

describe("contractEntryKey", () => {
  it("normalizes ticker, expiry, right, and strike into the symbolic key", () => {
    expect(contractEntryKey("MSFT", "20260717", "C", 410)).toBe("MSFT|20260717|C|410");
  });

  it("takes the first whitespace token of an OCC-form ticker", () => {
    expect(contractEntryKey("MSFT  260717C00410000", "20260717", "C", 410.0)).toBe(
      "MSFT|20260717|C|410",
    );
  });

  it("tolerates an ISO expiry stray (dashes stripped)", () => {
    expect(contractEntryKey("msft", "2026-07-17", "CALL", "410.0")).toBe(
      "MSFT|20260717|C|410",
    );
  });

  it("normalizes PUT to P and float strikes to their numeric form", () => {
    expect(contractEntryKey("EWY", "20261218", "PUT", "62.5")).toBe("EWY|20261218|P|62.5");
  });

  it("returns null when any component is missing or unparseable", () => {
    expect(contractEntryKey("", "20260717", "C", 410)).toBeNull();
    expect(contractEntryKey("MSFT", null, "C", 410)).toBeNull();
    expect(contractEntryKey("MSFT", "20260717", "?", 410)).toBeNull();
    expect(contractEntryKey("MSFT", "20260717", "C", "not-a-strike")).toBeNull();
  });
});

describe("buildContractEntryDates", () => {
  // Real production shape (Turso journal, MSFT $410C 20260717): the SAME
  // opening fill exists twice — a session-import row (plain ticker) and a
  // Flex-rehydrate row (OCC ticker) — so signed net qty never returns to
  // zero after the close (-50 open vs +25 close). The episode-start date is
  // unaffected; the builder must not require a return-to-zero.
  const msftDuplicatedOpenRows = [
    { ticker: "MSFT", expiry: "20260717", right: "C", strike: 410.0, action: "SELL_TO_OPEN", contracts: 25, date: "2026-07-02" },
    { ticker: "MSFT  260717C00410000", expiry: "20260717", right: "C", strike: 410.0, action: "SELL_TO_OPEN", contracts: 25, date: "2026-07-02" },
    { ticker: "MSFT", expiry: "20260717", right: "C", strike: 410.0, action: "BUY_OPTION", contracts: 14, date: "2026-07-08" },
    { ticker: "MSFT", expiry: "20260717", right: "C", strike: 410.0, action: "BUY_OPTION", contracts: 1, date: "2026-07-08" },
    { ticker: "MSFT", expiry: "20260717", right: "C", strike: 410.0, action: "BUY_OPTION", contracts: 10, date: "2026-07-08" },
  ];

  it("returns the opening date despite duplicated session + Flex open rows", () => {
    expect(buildContractEntryDates(msftDuplicatedOpenRows)).toEqual({
      "MSFT|20260717|C|410": "2026-07-02",
    });
  });

  it("returns the LAST open episode's date for a re-opened contract", () => {
    const rows = [
      { ticker: "PLTR", expiry: "20260821", right: "C", strike: 145, action: "BUY_OPTION", contracts: 10, date: "2026-06-01" },
      { ticker: "PLTR", expiry: "20260821", right: "C", strike: 145, action: "SELL_OPTION", contracts: 10, date: "2026-06-05" },
      { ticker: "PLTR", expiry: "20260821", right: "C", strike: 145, action: "BUY_OPTION", contracts: 5, date: "2026-06-20" },
    ];
    expect(buildContractEntryDates(rows)).toEqual({
      "PLTR|20260821|C|145": "2026-06-20",
    });
  });

  it("treats a sign flip through zero as a new episode start", () => {
    const rows = [
      { ticker: "AMD", expiry: "20260918", right: "P", strike: 120, action: "BUY_OPTION", contracts: 10, date: "2026-06-10" },
      { ticker: "AMD", expiry: "20260918", right: "P", strike: 120, action: "SELL_OPTION", contracts: 15, date: "2026-06-15" },
    ];
    expect(buildContractEntryDates(rows)).toEqual({
      "AMD|20260918|P|120": "2026-06-15",
    });
  });

  it("sorts rows by date before walking net quantity", () => {
    const rows = [
      { ticker: "TSLA", expiry: "20261016", right: "C", strike: 300, action: "SELL_OPTION", contracts: 5, date: "2026-06-30" },
      { ticker: "TSLA", expiry: "20261016", right: "C", strike: 300, action: "BUY_OPTION", contracts: 5, date: "2026-06-25" },
    ];
    expect(buildContractEntryDates(rows)).toEqual({
      "TSLA|20261016|C|300": "2026-06-25",
    });
  });

  it("merges ISO-expiry strays into the compact-expiry bucket", () => {
    const rows = [
      { ticker: "NVDA", expiry: "2026-08-21", right: "C", strike: 180, action: "BUY_OPTION", contracts: 3, date: "2026-06-26" },
      { ticker: "NVDA", expiry: "20260821", right: "C", strike: 180, action: "SELL_OPTION", contracts: 3, date: "2026-07-01" },
    ];
    expect(buildContractEntryDates(rows)).toEqual({
      "NVDA|20260821|C|180": "2026-06-26",
    });
  });

  it("skips rows missing contract fields, dates, or a signable action", () => {
    const rows = [
      { ticker: "MSFT", expiry: "20260717", right: "C", strike: 410, action: "SELL_TO_OPEN", contracts: 25, date: null },
      { ticker: "", expiry: "20260717", right: "C", strike: 410, action: "SELL_TO_OPEN", contracts: 25, date: "2026-07-02" },
      { ticker: "MSFT", expiry: "20260717", right: "C", strike: 410, action: "REVIEWED", contracts: 25, date: "2026-07-02" },
      { ticker: "MSFT", expiry: "20260717", right: "C", strike: 410, action: "SELL_TO_OPEN", contracts: 0, date: "2026-07-02" },
    ];
    expect(buildContractEntryDates(rows)).toEqual({});
  });

  it("keeps a date-only value when the row carries a full timestamp", () => {
    const rows = [
      { ticker: "META", expiry: "20260717", right: "P", strike: 600, action: "BUY_OPTION", contracts: 2, date: "2026-06-29T14:30:00Z" },
    ];
    expect(buildContractEntryDates(rows)).toEqual({
      "META|20260717|P|600": "2026-06-29",
    });
  });
});
