/**
 * Discover route staleness — naive ISO scan_time must be parsed as UTC.
 *
 * Producer: scripts/discover.py writes scan_time via datetime.now().isoformat()
 *   (no offset). Hetzner runs UTC. JS Date.parse() on naive ISO treats it as
 *   *local time*, shifting the parsed instant by the viewer's offset and
 *   making the freshness banner lie. The fix: parse via parseScanTime() so
 *   naive strings are coerced to UTC.
 *
 * STALE_THRESHOLD_SECONDS = 600 (10 min) inside the route.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";

// The route now reads BOTH sources and serves the fresher one (DUR-01).
// Pin the disk fallback to "absent" so these tests exercise the DB
// scan_time parse in isolation — the real data/discover.json on this
// machine would otherwise win the freshness comparison.
vi.mock("fs/promises", () => ({
  readFile: vi.fn().mockRejectedValue(new Error("ENOENT")),
}));
vi.mock("fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs")>();
  return {
    ...actual,
    statSync: vi.fn(() => {
      throw new Error("ENOENT");
    }),
  };
});

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS discover_snapshots (
  scan_time   TEXT NOT NULL,
  payload     TEXT NOT NULL
);
`;

let db: Client;

beforeEach(async () => {
  db = createClient({ url: ":memory:" });
  for (const stmt of SCHEMA_SQL.split(";").map((s) => s.trim()).filter(Boolean)) {
    await db.execute(stmt);
  }
  const dbModule = await import("../lib/db");
  dbModule.__setDbForTests(db);
});

afterEach(async () => {
  const dbModule = await import("../lib/db");
  dbModule.__resetDbForTests();
  db.close();
  vi.useRealTimers();
});

const PAYLOAD = JSON.stringify({
  discovery_time: "ignored-by-meta",
  candidates_found: 0,
  candidates: [],
});

async function insertSnapshot(scanTime: string): Promise<void> {
  await db.execute({
    sql: "INSERT INTO discover_snapshots (scan_time, payload) VALUES (?, ?)",
    args: [scanTime, PAYLOAD],
  });
}

describe("GET /api/discover — naive ISO scan_time is treated as UTC", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Anchor "now" to a fixed UTC moment. CDT viewer is UTC-5 — the bug
    // reproduces because Date.parse() on a naive string would shift the
    // parsed instant +5h (treating it as local then converting to UTC),
    // making fresh data look STALE in the user-reported direction (or
    // fresher than reality in the opposite direction depending on how
    // age is computed). Here we lock the clock so age math is exact.
    vi.setSystemTime(new Date("2026-05-09T02:03:00Z"));
  });

  it("naive scan_time produced 30s ago is NOT stale (was wrongly stale on local-time parse)", async () => {
    // 30 seconds before the anchored now.
    const naive = "2026-05-09T02:02:30.000000"; // no offset, UTC truth
    await insertSnapshot(naive);

    const { GET } = await import("../app/api/discover/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.cache_meta.is_stale).toBe(false);
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(0);
    expect(body.cache_meta.age_seconds).toBeLessThan(60);
  });

  it("naive scan_time produced 11 minutes ago IS stale", async () => {
    // 11 minutes before the anchored now (>10min threshold).
    const naive = "2026-05-09T01:52:00.000000";
    await insertSnapshot(naive);

    const { GET } = await import("../app/api/discover/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.age_seconds).toBeGreaterThan(600);
  });

  it("timezone-aware scan_time still parses correctly (regression: don't double-shift)", async () => {
    const aware = "2026-05-09T02:02:30+00:00"; // explicit UTC offset, 30s ago
    await insertSnapshot(aware);

    const { GET } = await import("../app/api/discover/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.cache_meta.is_stale).toBe(false);
    expect(body.cache_meta.age_seconds).toBeLessThan(60);
  });
});
