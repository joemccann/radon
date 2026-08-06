// Ensure Performance TWR tables exist (plan §4.2).
// Idempotent CREATE TABLE IF NOT EXISTS via the bounded dbExecute chokepoint
// (same Turso libsql pattern as every other route). Called on the first
// performance build so a host that hasn't run migration 0035 yet still
// creates nav_snapshots / external_flows / twr_subperiods.

import { dbExecute } from "./dbExecute";

const DDL_STATEMENTS: string[] = [
  `CREATE TABLE IF NOT EXISTS nav_snapshots (
    account_id    TEXT NOT NULL,
    report_date   TEXT NOT NULL,
    total_net_liq REAL NOT NULL,
    cash          REAL,
    stock         REAL,
    options       REAL,
    accrued_fees  REAL,
    PRIMARY KEY (account_id, report_date)
  )`,
  `CREATE INDEX IF NOT EXISTS idx_nav_snapshots_date ON nav_snapshots (report_date DESC)`,
  `CREATE TABLE IF NOT EXISTS external_flows (
    account_id  TEXT NOT NULL,
    report_date TEXT NOT NULL,
    amount      REAL NOT NULL,
    flow_type   TEXT NOT NULL,
    note        TEXT,
    PRIMARY KEY (account_id, report_date, flow_type)
  )`,
  `CREATE INDEX IF NOT EXISTS idx_external_flows_date ON external_flows (report_date DESC)`,
  `CREATE TABLE IF NOT EXISTS twr_subperiods (
    account_id  TEXT NOT NULL,
    report_date TEXT NOT NULL,
    b           REAL NOT NULL,
    e           REAL NOT NULL,
    c           REAL NOT NULL,
    r           REAL NOT NULL,
    cum_r       REAL NOT NULL,
    PRIMARY KEY (account_id, report_date)
  )`,
  `CREATE INDEX IF NOT EXISTS idx_twr_subperiods_date ON twr_subperiods (report_date DESC)`,
];

let ensured = false;

export async function ensurePerfTwrTables(): Promise<void> {
  if (ensured) return;
  for (const sql of DDL_STATEMENTS) {
    // Each DDL is a bounded read via dbExecute (2.75s transport timeout +
    // self-healing pool). CREATE TABLE IF NOT EXISTS is cheap and idempotent.
    await dbExecute({ sql, args: [] }, { label: "perf-twr-ensure", timeoutMs: 5_000 });
  }
  ensured = true;
}

// Test seam: reset the once-flag between tests that mock dbExecute.
export function __resetPerfTwrTablesForTests(): void {
  ensured = false;
}
