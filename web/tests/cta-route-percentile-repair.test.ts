import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createClient, type Client } from "@libsql/client";

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS menthorq_cta (
  date        TEXT    PRIMARY KEY,
  payload     TEXT    NOT NULL,
  fetched_at  TEXT    NOT NULL
);
`;

// The 2026-08-25 payload exactly as it sits in Turso. The main table's SPX and
// NQ percentiles were rounded to 0 by the vision extractor; the index table
// holds the same two rows on the fractional scale.
const AUG_25_PAYLOAD = {
  date: "2026-08-25",
  fetched_at: "2026-08-25T20:16:00Z",
  tables: {
    main: [
      { underlying: "E-Mini S&P 500 Index", position_today: 3.66, position_yesterday: 3.85, position_1m_ago: 1.69, percentile_1m: 0, percentile_3m: 0, percentile_1y: 0, z_score_3m: 1.48 },
      { underlying: "CME Nasdaq 100 Index", position_today: 2.52, position_yesterday: 2.69, position_1m_ago: 1.59, percentile_1m: 0, percentile_3m: 0, percentile_1y: 0, z_score_3m: 0.26 },
      { underlying: "Brent", position_today: -1.33, position_yesterday: -0.83, position_1m_ago: 0.99, percentile_1m: 5, percentile_3m: 2, percentile_1y: 5, z_score_3m: -1.89 },
    ],
    index: [
      { underlying: "E-Mini S&P 500 Index", position_today: 3.66, position_yesterday: 3.85, position_1m_ago: 1.69, percentile_1m: 0.43, percentile_3m: 0.81, percentile_1y: 0.88, z_score_3m: 1.48 },
      { underlying: "CME Nasdaq 100 Index", position_today: 2.52, position_yesterday: 2.69, position_1m_ago: 1.59, percentile_1m: 0.38, percentile_3m: 0.52, percentile_1y: 0.68, z_score_3m: 0.26 },
    ],
    commodity: [],
    currency: [],
  },
};

let db: Client;

beforeEach(async () => {
  db = createClient({ url: ":memory:" });
  for (const stmt of SCHEMA_SQL.split(";").map((s) => s.trim()).filter(Boolean)) {
    await db.execute(stmt);
  }
  const dbModule = await import("../lib/db");
  dbModule.__setDbForTests(db);
  await db.execute({
    sql: "INSERT INTO menthorq_cta (date, payload, fetched_at) VALUES (?, ?, ?)",
    args: ["2026-08-25", JSON.stringify(AUG_25_PAYLOAD), "2026-08-25T20:16:00Z"],
  });
});

afterEach(async () => {
  const dbModule = await import("../lib/db");
  dbModule.__resetDbForTests();
  db.close();
});

describe("CTA route percentile repair", () => {
  it("serves the repaired percentile, not the rounded 0 stored in Turso", async () => {
    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    expect(response.status).toBe(200);
    const data = await response.json();

    const spx = data.tables.main[0];
    expect(spx.underlying).toBe("E-Mini S&P 500 Index");
    expect(spx.percentile_3m).toBe(81);
    expect(spx.percentile_1m).toBe(43);
    expect(spx.percentile_1y).toBe(88);

    expect(data.tables.main[1].percentile_3m).toBe(52);
    // Untouched rows keep their stored values.
    expect(data.tables.main[2].percentile_3m).toBe(2);
    // Fractions are served on the 0-100 scale the UI reads.
    expect(data.tables.index[0].percentile_3m).toBe(81);
  });
});
