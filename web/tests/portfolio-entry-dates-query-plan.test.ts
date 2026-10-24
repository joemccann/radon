/**
 * @vitest-environment node
 *
 * `GET /api/portfolio?include=entry-dates` filters and groups the journal on
 * json_extract(payload, ...). Without an index matching those expressions
 * SQLite reads every journal row and builds a temp B-tree for the GROUP BY.
 * This runs the route against a real SQLite database built from every
 * automatic migration and checks the query plan of the SQL it actually sent.
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { createClient, type Client, type InStatement } from "@libsql/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MIGRATIONS_DIR = path.join(__dirname, "../../scripts/db/migrations");
const MANUAL_DIRECTIVE = "-- radon-migrate: manual";

const db = vi.hoisted(() => ({
  current: null as null | {
    execute: (s: unknown) => Promise<unknown>;
    batch: (s: unknown) => Promise<unknown>;
  },
}));
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => db.current }));
vi.mock("@/lib/radonApi", () => ({ radonFetch: vi.fn() }));

/** Same split rule as scripts/db/migrate.py `_split_statements`. */
function statementsOf(sql: string): string[] {
  return sql
    .split("\n")
    .map((line) => line.replace(/^\s*--.*$/, ""))
    .join("\n")
    .split(/;\s*$/m)
    .map((stmt) => stmt.trim())
    .filter(Boolean);
}

async function migratedDb(): Promise<Client> {
  const client = createClient({ url: ":memory:" });
  await client.execute(
    "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
  );
  const files = readdirSync(MIGRATIONS_DIR).filter((name) => /^\d+_.*\.sql$/.test(name)).sort();
  for (const file of files) {
    const sql = readFileSync(path.join(MIGRATIONS_DIR, file), "utf8");
    if (sql.split("\n").some((line) => line.trim() === MANUAL_DIRECTIVE)) continue;
    for (const stmt of statementsOf(sql)) await client.execute(stmt);
  }
  return client;
}

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

let client: Client;
let sent: string[];

beforeEach(async () => {
  vi.resetModules();
  client = await migratedDb();
  await client.execute({
    sql: "INSERT INTO portfolio_snapshots (taken_at, payload) VALUES (?, ?)",
    args: [SNAPSHOT.last_sync, JSON.stringify(SNAPSHOT)],
  });
  const fills = [
    ["t1", { ticker: "AAPL", date: "2026-09-01", action: "BUY", contracts: 100 }, "2026-09-01T14:00:00Z"],
    ["t2", { ticker: "AAPL", date: "2026-09-03", action: "SELL", contracts: 50 }, null],
    ["t3", { ticker: "META", expiry: "20261120", right: "C", strike: 575, action: "BUY", contracts: 2, date: "2026-09-02" }, "2026-09-02T15:00:00Z"],
    ["t4", { ticker: "META", expiry: "20261120", right: "C", strike: 575, action: "BUY", contracts: 1, date: "2026-09-04" }, "2026-09-04T15:00:00Z"],
    ["t5", { note: "row without a ticker" }, "2026-09-05T15:00:00Z"],
  ] as const;
  for (const [id, payload, filledAt] of fills) {
    await client.execute({
      sql: "INSERT INTO journal (trade_id, payload, filled_at, written_at) VALUES (?, ?, ?, ?)",
      args: [id, JSON.stringify(payload), filledAt, "2026-09-06T00:00:00Z"],
    });
  }
  sent = [];
  db.current = {
    execute: (statement: unknown) => {
      const stmt = statement as InStatement;
      sent.push(typeof stmt === "string" ? stmt : stmt.sql);
      return client.execute(stmt);
    },
    batch: (statements: unknown) => {
      const stmts = statements as InStatement[];
      return Promise.all(stmts.map((statement) => {
        sent.push(typeof statement === "string" ? statement : statement.sql);
        return client.execute(statement);
      }));
    },
  };
});

afterEach(() => {
  client.close();
});

async function journalStatements(): Promise<{ body: Record<string, unknown>; sql: string[] }> {
  const { GET } = await import("../app/api/portfolio/route");
  const res = await GET(new Request("http://localhost/api/portfolio?include=entry-dates"));
  expect(res.status).toBe(200);
  const body = (await res.json()) as Record<string, unknown>;
  const sql = sent.filter((stmt) => /FROM\s+journal/i.test(stmt));
  expect(sql).toHaveLength(2);
  return { body, sql };
}

async function planOf(sql: string): Promise<string[]> {
  const result = await client.execute(`EXPLAIN QUERY PLAN ${sql}`);
  return result.rows.map((row) => String((row as unknown as { detail: string }).detail));
}

describe("GET /api/portfolio?include=entry-dates journal query plans", () => {
  it("serves the per-ticker MAX(date) query from idx_journal_ticker without a temp B-tree", async () => {
    const { sql } = await journalStatements();
    const tradeLogSql = sql.find((stmt) => /GROUP BY/i.test(stmt))!;
    const plan = await planOf(tradeLogSql);

    expect(plan.join(" | ")).toContain("idx_journal_ticker");
    expect(plan.join(" | ")).not.toMatch(/TEMP B-TREE/i);
    expect(plan.filter((line) => /^SCAN journal$/.test(line.trim()))).toEqual([]);
  });

  it("serves the option-contract query from idx_journal_option_contract instead of a table scan", async () => {
    const { sql } = await journalStatements();
    const contractSql = sql.find((stmt) => /opt_right/i.test(stmt))!;
    const plan = await planOf(contractSql);

    expect(plan.join(" | ")).toContain("idx_journal_option_contract");
    expect(plan.filter((line) => /^SCAN journal$/.test(line.trim()))).toEqual([]);
  });

  it("returns the same entry-date maps through the indexes", async () => {
    const { body } = await journalStatements();

    expect(body.trade_log_dates).toEqual({
      AAPL: "2026-09-03",
      META: "2026-09-04T15:00:00Z",
    });
    expect(body.contract_open_dates).toEqual({ "META|20261120|C|575": "2026-09-02" });
  });
});
