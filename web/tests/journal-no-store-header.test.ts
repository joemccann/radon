/**
 * Bug guard: /api/journal must return Cache-Control: no-store on every
 * response so browsers and intermediaries never serve a stale snapshot.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

const mockReadJournalFromDb = vi.fn();
const mockImportLatestReconciliationToJournal = vi.fn();
vi.mock("@/lib/journalDb", () => ({
  readJournalFromDb: mockReadJournalFromDb,
  importLatestReconciliationToJournal: mockImportLatestReconciliationToJournal,
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: vi.fn().mockResolvedValue({ ok: true }),
}));

function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

describe("/api/journal — Cache-Control: no-store", () => {
  beforeEach(() => {
    vi.resetModules();
    mockReadJournalFromDb.mockResolvedValue({
      trades: [{ ticker: "AAPL", filled_at: "2026-05-08", date: "2026-05-08" }],
    });
    mockImportLatestReconciliationToJournal.mockResolvedValue({ imported: 0, skipped: 0, trades: [] });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("GET sets no-store on the success path", async () => {
    const { GET } = await import("../app/api/journal/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("GET sets no-store on the 500 error envelope", async () => {
    mockReadJournalFromDb.mockRejectedValue(new Error("DB unavailable"));
    const { GET } = await import("../app/api/journal/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("POST sets no-store on the reconcile/import success path", async () => {
    const { POST } = await import("../app/api/journal/route");
    const res = await POST();
    expectNoStore(res);
  });
});
