import { describe, expect, it } from "vitest";

import { parseJournalRows } from "@/app/api/blotter/route";

describe("blotter journal row quarantine", () => {
  it("malformed row cannot turn a nonempty journal into empty success", () => {
    const valid = JSON.stringify({ ticker: "AAPL", action: "BUY", quantity: 1 });
    expect(parseJournalRows([
      { payload: "{bad", filled_at: "2026-08-13T10:00:00Z" },
      { payload: valid, filled_at: "2026-08-13T10:01:00Z" },
    ])).toHaveLength(1);
    expect(() => parseJournalRows([{ payload: "{bad" }])).toThrow(
      "no trustworthy rows",
    );
  });
});
