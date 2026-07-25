/**
 * @vitest-environment node
 *
 * /api/bpi — Bullish Percent Index snapshot route (regime BULLISH % tab).
 *
 * GET-only: the scan runs on the Mon-Fri 21:30 UTC radon-bpi timer, so
 * there is no client-side scan POST. Reads through the dbFirstRead
 * chokepoint — the three Turso bpi_snapshots payload rows first, disk
 * data/bpi.json fallback — and ALWAYS returns 200 with all three index
 * keys; an absent index is its `{missing: true, index_symbol}` shape,
 * never a 4xx.
 *
 * Mechanics mirror rv-ratio-route.test.ts: @/lib/db backed by a real
 * in-memory libsql client seeded with the bpi_snapshots schema; fs reads
 * mocked; every date window-relative via the shared fixture builders.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";

import type { BpiPayload } from "@/lib/bpi";
import { buildBpiPayloadFixture } from "./bpi-fixture";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: () => mockGetDb(),
}));

const mockReadFile = vi.fn();
vi.mock("fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs/promises")>();
  return {
    ...actual,
    readFile: (...args: unknown[]) => mockReadFile(...args),
  };
});

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

type Json = Record<string, unknown>;

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE bpi_snapshots (
    index_symbol TEXT PRIMARY KEY,
    taken_at     TEXT NOT NULL,
    payload      TEXT NOT NULL)`);
}

async function insertSnapshot(payload: BpiPayload, takenAt?: string): Promise<void> {
  await db.execute({
    sql: `INSERT OR REPLACE INTO bpi_snapshots (index_symbol, taken_at, payload) VALUES (?, ?, ?)`,
    args: [payload.index_symbol, takenAt ?? payload.taken_at, JSON.stringify(payload)],
  });
}

async function jsonOf(res: Response): Promise<Json> {
  return (await res.json()) as Json;
}

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
  mockReadFile.mockRejectedValue(ENOENT);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/bpi", () => {
  it("returns 200 with all three indices missing:true (never a 4xx) when both sources are absent", async () => {
    const { GET } = await import("../app/api/bpi/route");
    const res = await GET();

    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      generated_at: null,
      indices: {
        NDX: { missing: true, index_symbol: "NDX" },
        SPX: { missing: true, index_symbol: "SPX" },
        RUT: { missing: true, index_symbol: "RUT" },
      },
    });
  });

  it("sets Cache-Control: no-store on every response", async () => {
    const { GET } = await import("../app/api/bpi/route");
    const res = await GET();
    expect((res.headers.get("Cache-Control") ?? "").toLowerCase()).toContain("no-store");
  });

  it("serves the three bpi_snapshots payload rows with generated_at = newest taken_at", async () => {
    const ndx = buildBpiPayloadFixture({ symbol: "NDX" });
    const spx = buildBpiPayloadFixture({ symbol: "SPX" });
    const rut = buildBpiPayloadFixture({ symbol: "RUT", endSessionsAgo: 1 });
    await insertSnapshot(ndx);
    await insertSnapshot(spx);
    await insertSnapshot(rut);

    const { GET } = await import("../app/api/bpi/route");
    const json = await jsonOf(await GET());

    expect(json.generated_at).toBe(ndx.taken_at);
    const indices = json.indices as Record<string, Json>;
    expect(indices.NDX.index_symbol).toBe("NDX");
    expect(indices.NDX.bpi).toBe(ndx.bpi);
    expect(indices.SPX.members).toBe(500);
    expect(indices.RUT.as_of_session).toBe(rut.as_of_session);
  });

  it("fills indices absent from the DB with their missing:true shape", async () => {
    await insertSnapshot(buildBpiPayloadFixture({ symbol: "NDX" }));

    const { GET } = await import("../app/api/bpi/route");
    const json = await jsonOf(await GET());

    const indices = json.indices as Record<string, Json>;
    expect(indices.NDX.missing).toBeUndefined();
    expect(indices.SPX).toEqual({ missing: true, index_symbol: "SPX" });
    expect(indices.RUT).toEqual({ missing: true, index_symbol: "RUT" });
  });

  it("falls back to disk data/bpi.json when Turso has no rows", async () => {
    const ndx = buildBpiPayloadFixture({ symbol: "NDX" });
    mockReadFile.mockResolvedValue(
      JSON.stringify({ generated_at: ndx.taken_at, indices: { NDX: ndx } }),
    );

    const { GET } = await import("../app/api/bpi/route");
    const json = await jsonOf(await GET());

    expect(json.generated_at).toBe(ndx.taken_at);
    const indices = json.indices as Record<string, Json>;
    expect(indices.NDX.bpi).toBe(ndx.bpi);
    expect(indices.SPX).toEqual({ missing: true, index_symbol: "SPX" });
    expect(mockReadFile).toHaveBeenCalledWith(
      expect.stringContaining("data"),
      "utf-8",
    );
    expect(String(mockReadFile.mock.calls[0][0])).toContain("bpi.json");
  });

  it("serves the newer DB snapshot over an older disk cache", async () => {
    const fresh = buildBpiPayloadFixture({ symbol: "NDX" });
    await insertSnapshot(fresh);
    const stale = buildBpiPayloadFixture({ symbol: "NDX", endSessionsAgo: 5 });
    mockReadFile.mockResolvedValue(
      JSON.stringify({ generated_at: stale.taken_at, indices: { NDX: stale } }),
    );

    const { GET } = await import("../app/api/bpi/route");
    const json = await jsonOf(await GET());

    expect((json.indices as Record<string, Json>).NDX.as_of_session).toBe(fresh.as_of_session);
  });

  it("serves the disk snapshot when it is newer than the DB rows", async () => {
    const stale = buildBpiPayloadFixture({ symbol: "NDX", endSessionsAgo: 5 });
    await insertSnapshot(stale);
    const fresh = buildBpiPayloadFixture({ symbol: "NDX" });
    mockReadFile.mockResolvedValue(
      JSON.stringify({ generated_at: fresh.taken_at, indices: { NDX: fresh } }),
    );

    const { GET } = await import("../app/api/bpi/route");
    const json = await jsonOf(await GET());

    expect((json.indices as Record<string, Json>).NDX.as_of_session).toBe(fresh.as_of_session);
  });
});

describe("route cache contract", () => {
  it("declares force-dynamic and the nodejs runtime", async () => {
    const route = await import("../app/api/bpi/route");
    expect(route.dynamic).toBe("force-dynamic");
    expect(route.runtime).toBe("nodejs");
  });
});
