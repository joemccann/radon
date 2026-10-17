/**
 * @vitest-environment node
 *
 * The two journal queries behind `GET /api/portfolio?include=entry-dates`
 * logged `error.message` verbatim. `web/lib/dbExecute.ts` scrubs its own
 * diagnostic and then rethrows the ORIGINAL error, and that scrubber's pattern
 * list (libsql/Turso URLs, bearer and auth tokens, API keys, JWTs, account ids)
 * is this repository's own statement that those strings are secret-bearing, so
 * the route's unscrubbed copy put them in the server log.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SNAPSHOT = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-10-09T20:00:00Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
};

const db = vi.hoisted(() => ({ fail: null as null | Error }));
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: () => ({
    // Only the journal reads fail: the snapshot must still load, or the route
    // 503s before it ever reaches the two queries under test.
    execute: async (statement: unknown) => {
      const sql =
        typeof statement === "string"
          ? statement
          : ((statement as { sql: string }).sql ?? "");
      if (/journal/i.test(sql) && db.fail) throw db.fail;
      if (/portfolio_snapshots/i.test(sql)) {
        return {
          rows: [
            {
              taken_at: SNAPSHOT.last_sync,
              payload: JSON.stringify(SNAPSHOT),
            },
          ],
          columns: ["taken_at", "payload"],
        };
      }
      return { rows: [], columns: [] };
    },
  }),
}));
vi.mock("@/lib/radonApi", () => ({ radonFetch: vi.fn() }));

const SECRET_URL = "libsql://radon-joemccann.aws-us-west-2.turso.io";
const SECRET_TOKEN = "Bearer eyJhbGciOiJFZERTQSJ9.payloadpayload.signature";

let warnings: string[];

beforeEach(() => {
  vi.resetModules();
  warnings = [];
  vi.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
    warnings.push(args.map(String).join(" "));
  });
  db.fail = new Error(`SQLITE_UNKNOWN: ${SECRET_URL} rejected ${SECRET_TOKEN}`);
});

afterEach(() => {
  vi.restoreAllMocks();
  db.fail = null;
});

describe("GET /api/portfolio journal failures", () => {
  it("never logs the raw database error", async () => {
    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET(
      new Request("http://localhost/api/portfolio?include=entry-dates"),
    );

    expect(res.status).toBeLessThan(500);
    const journalWarnings = warnings.filter((line) =>
      line.includes("journal query failed"),
    );
    expect(journalWarnings.length).toBeGreaterThan(0);
    for (const line of journalWarnings) {
      expect(line).not.toContain(SECRET_URL);
      expect(line).not.toContain(SECRET_TOKEN);
      expect(line).toContain("[redacted");
    }
  });
});
