#!/usr/bin/env node
/**
 * Mirror the latest market-analytics snapshots from the PROD Turso into the
 * DEMO Turso, so demo.radon.run's scanner / regime / CTA surfaces show real
 * sample data instead of empty panels (and the TEST_MODE scan guards in
 * scripts/api/demo_scan.py have snapshots to serve).
 *
 * Market analytics ONLY — never account data. The demo portfolio / journal /
 * orders stay the synthetic demo_seed.py fixtures.
 *
 * Isolation: PROD-SIDE job — the only place holding BOTH credential sets, same
 * contract as mirror_newsfeed_to_demo.js. The demo deployment never holds prod
 * creds.
 *
 * Source: TURSO_DB_URL + TURSO_AUTH_TOKEN (prod).
 * Dest:   TURSO_DEMO_DB_URL + TURSO_DEMO_AUTH_TOKEN (demo).
 */
import { createClient } from "@libsql/client";

const PROD_MARKER = "radon-joemccann";
const DEMO_MARKER = "radon-demo";

// Latest single row per table.
const LATEST_ONE = [
  { table: "scanner_snapshots", orderCol: "scan_time" },
  { table: "discover_snapshots", orderCol: "scan_time" },
  { table: "discover_sp500_snapshots", orderCol: "scan_time" },
  { table: "theta_harvester_snapshots", orderCol: "scan_time" },
  { table: "strength_confirmation_snapshots", orderCol: "scan_time" },
  { table: "vcg_snapshots", orderCol: "scan_time" },
  { table: "gamma_rotation_snapshots", orderCol: "scan_time" },
  { table: "oi_changes", orderCol: "scan_time" },
];

// Latest row per key (multiple services / tickers share one table).
const PER_KEY = [
  { table: "scan_snapshots", key: "service", orderCol: "scan_time" },
  { table: "gex_snapshots", key: "ticker", orderCol: "scan_time" },
];

// Date-keyed history windows (regime charts need a run of sessions).
const HISTORY = [
  { table: "cri_snapshots", orderCol: "date", limit: 30 },
  { table: "breadth_snapshots", orderCol: "date", limit: 30 },
  { table: "menthorq_cta", orderCol: "date", limit: 10 },
];

// This table can contain portfolio-derived flow rows and must never exist in
// the public demo database. Keep the purge in the recurring job so previously
// mirrored rows are removed after deployment as well as excluded going forward.
const PURGED_ACCOUNT_TABLES = ["flow_analysis_snapshots"];

// libsql:// -> https:// so the client uses the stateless HTTP transport.
function toHttp(url) {
  if (!url) return url;
  if (url.startsWith("libsql://")) return `https://${url.slice("libsql://".length)}`;
  if (url.startsWith("wss://")) return `https://${url.slice("wss://".length)}`;
  if (url.startsWith("ws://")) return `http://${url.slice("ws://".length)}`;
  return url;
}

function upsertStatements(table, columns, rows) {
  const cols = columns.filter((c) => c !== "rn");
  const placeholders = cols.map(() => "?").join(", ");
  const sql = `INSERT OR REPLACE INTO ${table} (${cols.join(", ")}) VALUES (${placeholders})`;
  return rows.map((r) => ({ sql, args: cols.map((c) => r[c] ?? null) }));
}

async function mirrorQuery(src, dst, table, sql, pruneSql) {
  let result;
  try {
    result = await src.execute(sql);
  } catch (err) {
    console.warn(`[mirror] SKIP ${table} (source read failed: ${err.message})`);
    throw new Error(`${table} source read failed`);
  }
  if (result.rows.length === 0) {
    console.warn(`[mirror] SKIP ${table} (no source rows)`);
    return 0;
  }
  try {
    const statements = upsertStatements(table, result.columns, result.rows);
    if (pruneSql) statements.push(pruneSql);
    await dst.batch(statements, "write");
  } catch (err) {
    console.warn(`[mirror] FAIL ${table} (dest write failed: ${err.message})`);
    throw new Error(`${table} destination write failed`);
  }
  console.log(`[mirror] ${table}: ${result.rows.length} row(s)`);
  return result.rows.length;
}

async function main() {
  const srcUrl = process.env.TURSO_DB_URL;
  const srcToken = process.env.TURSO_AUTH_TOKEN;
  const dstUrl = process.env.TURSO_DEMO_DB_URL;
  const dstToken = process.env.TURSO_DEMO_AUTH_TOKEN;

  if (!srcUrl || !srcToken) throw new Error("TURSO_DB_URL + TURSO_AUTH_TOKEN (prod source) required");
  if (!dstUrl || !dstToken) throw new Error("TURSO_DEMO_DB_URL + TURSO_DEMO_AUTH_TOKEN (demo dest) required");
  if (!dstUrl.includes(DEMO_MARKER)) {
    throw new Error(`dest ${dstUrl} is not the demo DB (missing '${DEMO_MARKER}') — refusing`);
  }
  if (dstUrl.replace(DEMO_MARKER, "").includes(PROD_MARKER) || dstUrl === srcUrl) {
    throw new Error("dest looks like prod / equals source — refusing to mirror");
  }

  const src = createClient({ url: toHttp(srcUrl), authToken: srcToken });
  const dst = createClient({ url: toHttp(dstUrl), authToken: dstToken });

  for (const table of PURGED_ACCOUNT_TABLES) {
    try {
      await dst.execute(`DELETE FROM ${table}`);
      console.log(`[mirror] purged account-derived table: ${table}`);
    } catch (err) {
      console.warn(`[mirror] SKIP purge ${table} (dest write failed: ${err.message})`);
    }
  }

  let total = 0;
  const failures = [];
  for (const { table, orderCol } of LATEST_ONE) {
    try {
      total += await mirrorQuery(
        src, dst, table,
        `SELECT * FROM ${table} ORDER BY ${orderCol} DESC LIMIT 1`,
        `DELETE FROM ${table} WHERE ${orderCol} NOT IN (SELECT ${orderCol} FROM ${table} ORDER BY ${orderCol} DESC LIMIT 1)`,
      );
    } catch { failures.push(table); }
  }
  for (const { table, key, orderCol } of PER_KEY) {
    try {
      total += await mirrorQuery(
        src, dst, table,
        `SELECT * FROM (
           SELECT *, ROW_NUMBER() OVER (PARTITION BY ${key} ORDER BY ${orderCol} DESC) AS rn
           FROM ${table}
         ) WHERE rn = 1`,
        `DELETE FROM ${table} WHERE (${key}, ${orderCol}) NOT IN (` +
          `SELECT ${key}, MAX(${orderCol}) FROM ${table} GROUP BY ${key})`,
      );
    } catch { failures.push(table); }
  }
  for (const { table, orderCol, limit } of HISTORY) {
    try {
      total += await mirrorQuery(
        src, dst, table,
        `SELECT * FROM ${table} ORDER BY ${orderCol} DESC LIMIT ${limit}`,
        `DELETE FROM ${table} WHERE ${orderCol} NOT IN (SELECT ${orderCol} FROM ${table} ORDER BY ${orderCol} DESC LIMIT ${limit})`,
      );
    } catch { failures.push(table); }
  }
  if (failures.length) throw new Error(`required table failures: ${failures.join(", ")}`);
  console.log(`[mirror] done — ${total} row(s) mirrored`);
}

main().catch((err) => {
  console.error("[mirror] FAILED:", err.message);
  process.exit(1);
});
