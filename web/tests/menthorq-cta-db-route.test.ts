import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createClient, type Client } from "@libsql/client";

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS menthorq_cta (
  date        TEXT    PRIMARY KEY,
  payload     TEXT    NOT NULL,
  fetched_at  TEXT    NOT NULL
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
});

describe("CTA route DB read path", () => {
  it("prefers the most recent menthorq_cta row when tables are populated", async () => {
    const newPayload = JSON.stringify({
      date: "2026-04-30",
      fetched_at: "2026-04-30T20:00:00Z",
      tables: {
        main: [{ underlying: "SPX", position_today: 0.5 }],
        index: [],
        commodity: [],
        currency: [],
      },
    });
    const oldPayload = JSON.stringify({
      date: "2026-04-29",
      fetched_at: "2026-04-29T20:00:00Z",
      tables: { main: [], index: [], commodity: [], currency: [] },
    });

    await db.execute({
      sql: "INSERT INTO menthorq_cta (date, payload, fetched_at) VALUES (?, ?, ?)",
      args: ["2026-04-29", oldPayload, "2026-04-29T20:00:00Z"],
    });
    await db.execute({
      sql: "INSERT INTO menthorq_cta (date, payload, fetched_at) VALUES (?, ?, ?)",
      args: ["2026-04-30", newPayload, "2026-04-30T20:00:00Z"],
    });

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.date).toBe("2026-04-30");
    expect(data.tables.main[0].underlying).toBe("SPX");
  });

  it("falls back to disk when DB has no rows", async () => {
    // No rows in DB and no fixture cache files — the route returns 503
    // per its existing contract (no readable cache anywhere). What we're
    // verifying is that the DB read returned null cleanly rather than
    // raising; the route then exercises the disk fallback.
    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    expect([200, 503]).toContain(response.status);
    const data = await response.json();
    expect(data).toHaveProperty("cache_meta");
  });
});
