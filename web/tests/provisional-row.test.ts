/** @vitest-environment node */
import { describe, expect, it } from "vitest";
import { appendProvisionalRow, provisionalNote, type LiveSnapshot } from "@/lib/provisionalRow";

type Row = { date: string; value: number };
const official = { series: [{ date: "2026-10-16", value: 1 }], data_date: "2026-10-16", count: 1, current: { date: "2026-10-16", value: 1 } };
const live = (row: Row | null, extra: Partial<LiveSnapshot<Row>> = {}): LiveSnapshot<Row> =>
  ({ scan_time: "2026-10-19T19:00:00Z", row, source: row ? "cboe_delayed" : null, quote_time: "2026-10-19T14:45:00", ...extra });

describe("appendProvisionalRow", () => {
  it("appends a newer row, defaulting current to the row", () => {
    const merged = appendProvisionalRow(official, live({ date: "2026-10-19", value: 2 }));
    expect(merged.series.map((r) => r.date)).toEqual(["2026-10-16", "2026-10-19"]);
    expect(merged).toMatchObject({ count: 2, data_date: "2026-10-19", current: { date: "2026-10-19", value: 2 } });
    expect(merged.provisional).toEqual({ source: "cboe_delayed", quote_time: "2026-10-19T14:45:00", scan_time: "2026-10-19T19:00:00Z" });
    expect("as_of" in merged).toBe(false);
    expect("status" in merged).toBe(false);
  });
  it("uses the writer's current card and keeps as_of and status in step", () => {
    const merged = appendProvisionalRow({ ...official, as_of: "2026-10-16", status: "stale_source", expected_session: "2026-10-19" },
      live({ date: "2026-10-19", value: 2 }, { current: { card: true } }));
    expect(merged).toMatchObject({ current: { card: true }, as_of: "2026-10-19", status: "ok" });
  });
  it("leaves the payload untouched without a newer row", () => {
    expect(appendProvisionalRow(official, null)).toBe(official);
    expect(appendProvisionalRow(official, live(null))).toBe(official);
    expect(appendProvisionalRow(official, live({ date: "2026-10-16", value: 9 }))).toBe(official);
  });
});

describe("provisionalNote", () => {
  it("names Cboe delayed quotes at their ET quote time", () => {
    expect(provisionalNote({ source: "cboe_delayed", quote_time: "2026-10-19T14:45:00", scan_time: "x" }))
      .toBe("Current session is provisional: Cboe 15-minute delayed quotes as of 14:45 ET, until the official close posts.");
  });
  it("names IB bars at the scan time in ET when there is no quote time", () => {
    expect(provisionalNote({ source: "ib", quote_time: null, scan_time: "2026-10-19T19:05:00Z" }))
      .toBe("Current session is provisional: IB index bars as of 15:05 ET, until the official close posts.");
  });
});
