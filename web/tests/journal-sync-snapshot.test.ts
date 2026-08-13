import { beforeEach, describe, expect, it, vi } from "vitest";

const requireRouteAccess = vi.fn();
const radonFetch = vi.fn();
const importSnapshot = vi.fn();

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess }));
vi.mock("@/lib/radonApi", () => ({ radonFetch }));
vi.mock("@/lib/journalDb", () => ({
  importReconciliationSnapshotToJournal: importSnapshot,
}));

describe("journal sync snapshot identity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    requireRouteAccess.mockResolvedValue({ ok: true, principal: "operator" });
  });

  it("sync_imports_only_the_fresh_returned_reconciliation_snapshot", async () => {
    const snapshotAt = new Date().toISOString();
    radonFetch.mockResolvedValue({ ok: true, snapshot_at: snapshotAt });
    importSnapshot.mockResolvedValue({ imported: 1, skipped: 0 });
    const { POST } = await import("@/app/api/journal/sync/route");

    const response = await POST();

    expect(response.status).toBe(200);
    expect(importSnapshot).toHaveBeenCalledWith(snapshotAt);
  });

  it("refuses a stale or missing snapshot identity", async () => {
    radonFetch.mockResolvedValue({ ok: true, snapshot_at: "2020-01-01T00:00:00Z" });
    const { POST } = await import("@/app/api/journal/sync/route");

    const response = await POST();

    expect(response.status).toBe(500);
    expect(importSnapshot).not.toHaveBeenCalled();
  });
});
