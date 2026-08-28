/**
 * R-375 / REL-126(c): a journal read returning ZERO rows is not a flat book.
 *
 * `readJournalRows` returns null on `rows.length === 0`, `buildFromJournal`
 * returns null, and GET fell to `emptyBlotter()` — `closed_trades: 0,
 * realized_pnl: 0, as_of: ""` served with HTTP 200 as authoritative. A Turso
 * replica pointed at the wrong database, or a journal not yet rehydrated, was
 * indistinguishable from a genuinely flat book: `useBlotter`'s
 * `extractTimestamp` maps the empty string to null so no timestamp
 * contradicts it, and POST returns 404 unconditionally so the operator cannot
 * force a rebuild.
 */
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

const mockExecute = vi.fn();
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: () => ({ execute: mockExecute }),
}));

vi.mock("@/lib/routeAccess", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, requireRouteAccess: vi.fn().mockResolvedValue({ ok: true }) };
});

beforeEach(() => {
  vi.resetModules();
  mockExecute.mockReset();
});
afterEach(() => vi.clearAllMocks());

describe("/api/blotter — zero rows is not an authoritative empty book", () => {
  it("marks a zero-row journal read as missing rather than flat", async () => {
    mockExecute.mockResolvedValue({ rows: [] });
    const { GET } = await import("../app/api/blotter/route");
    const res = await GET();
    const body = (await res.json()) as Record<string, unknown>;

    expect(res.status).toBe(200);
    expect(body.missing).toBe(true);
    expect(res.headers.get("X-Radon-Stale")).toBe("1");
  });

  it("does not mark a genuinely populated journal as missing", async () => {
    mockExecute.mockResolvedValue({
      rows: [
        {
          payload: JSON.stringify({
            ticker: "AAPL",
            action: "BUY_OPTION",
            contracts: 1,
            fill_price: 1.0,
            date: "2026-08-10",
            ib_exec_id: "e1",
            strike: 200,
            right: "C",
            expiry: "20261016",
          }),
          filled_at: "2026-08-10",
        },
      ],
    });
    const { GET } = await import("../app/api/blotter/route");
    const res = await GET();
    const body = (await res.json()) as Record<string, unknown>;

    expect(body.missing).toBeUndefined();
    expect(res.headers.get("X-Radon-Stale")).toBeNull();
  });
});
