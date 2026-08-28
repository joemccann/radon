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
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { recordServiceHealth } from "./writer.js";

// R-325: this unit fired every weekday at 21:45 UTC and wrote NO
// service_health row, so it sat in neither watchdog catalog and a demo mirror
// that had stopped mirroring was invisible on both sides.
const SERVICE_NAME = "demo-mirror";
const PROD_MARKER = "radon-joemccann";
const DEMO_MARKER = "radon-demo";
const DEFAULT_MAX_ATTEMPTS = 3;
const RETRY_DELAYS_MS = [500, 2_000];

// Latest single row per table.
export const LATEST_ONE = [
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
export const PER_KEY = [
  { table: "scan_snapshots", key: "service", orderCol: "scan_time" },
  { table: "gex_snapshots", key: "ticker", orderCol: "scan_time" },
  // Equibles per-ticker decks. orderCol matches the column each API route
  // sorts on so the mirrored row is the one the demo actually serves:
  // equibles-smart-money-13f orders by report_date DESC, filing-forensics
  // keys on the ticker PK and reports as_of.
  // equibles_13f_holders is deliberately NOT mirrored — the holder rows are
  // write-only depth (retention.py prunes them; no route or component queries
  // the table). The panel renders payload.holders out of the snapshot JSON.
  { table: "equibles_13f_snapshots", key: "ticker", orderCol: "report_date" },
  { table: "equibles_filing_forensics", key: "ticker", orderCol: "as_of" },
];

// Date-keyed history windows (regime charts need a run of sessions).
export const HISTORY = [
  { table: "cri_snapshots", orderCol: "date", limit: 30 },
  { table: "breadth_snapshots", orderCol: "date", limit: 30 },
  { table: "menthorq_cta", orderCol: "date", limit: 10 },
];

// This table can contain portfolio-derived flow rows and must never exist in
// the public demo database. Keep the purge in the recurring job so previously
// mirrored rows are removed after deployment as well as excluded going forward.
export const PURGED_ACCOUNT_TABLES = ["flow_analysis_snapshots"];

// libsql:// -> https:// so the client uses the stateless HTTP transport.
export function toHttp(url) {
  if (!url) return url;
  if (url.startsWith("libsql://")) return `https://${url.slice("libsql://".length)}`;
  if (url.startsWith("wss://")) return `https://${url.slice("wss://".length)}`;
  if (url.startsWith("ws://")) return `http://${url.slice("ws://".length)}`;
  return url;
}

export function isTransientTursoError(error) {
  if (!error) return false;
  if (error instanceof TypeError) return true;
  const status = Number(error?.status ?? error?.statusCode ?? 0);
  const code = String(error?.code ?? "").toUpperCase();
  const message = String(error?.message ?? error);
  const lower = message.toLowerCase();
  return (
    [408, 425, 429].includes(status)
    || status >= 500
    || /^(HRANA_|SERVER_|WEBSOCKET_)/.test(code)
    || ["ECONNRESET", "ECONNREFUSED", "EAI_AGAIN", "ETIMEDOUT"].includes(code)
    || error?.name === "AbortError"
    || error?.name === "TimeoutError"
    || /fetch failed|timed ?out|timeout|aborted|network|socket|econn|etimedout|eai_again|epipe/i.test(message)
    || /http status 5\d\d/i.test(message)
    || lower.includes("server_error")
  );
}

async function retryOperation({
  phase,
  operation,
  maxAttempts,
  sleep,
  log,
  now,
  runId,
}) {
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      const retrying = attempt < maxAttempts && isTransientTursoError(error);
      log({
        timestamp: now(),
        run_id: runId,
        phase,
        event: retrying ? "retry" : "failed",
        attempt,
        error: String(error?.message ?? error),
      });
      if (!retrying) throw error;
      const delay = RETRY_DELAYS_MS[Math.min(attempt - 1, RETRY_DELAYS_MS.length - 1)];
      await sleep(delay);
    }
  }
  throw new Error(`${phase} exhausted without a result`);
}

function upsertStatements(table, columns, rows) {
  const cols = columns.filter((c) => c !== "rn");
  const placeholders = cols.map(() => "?").join(", ");
  const sql = `INSERT OR REPLACE INTO ${table} (${cols.join(", ")}) VALUES (${placeholders})`;
  return rows.map((r) => ({ sql, args: cols.map((c) => r[c] ?? null) }));
}

async function mirrorQuery(src, dst, table, sql, pruneSql, opts) {
  const { maxAttempts, sleep, log, now, runId } = opts;
  let result;
  try {
    result = await retryOperation({
      phase: `${table}:source_read`,
      operation: () => src.execute(sql),
      maxAttempts,
      sleep,
      log,
      now,
      runId,
    });
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
    await retryOperation({
      phase: `${table}:destination_write`,
      operation: () => dst.batch(statements, "write"),
      maxAttempts,
      sleep,
      log,
      now,
      runId,
    });
  } catch (err) {
    console.warn(`[mirror] FAIL ${table} (dest write failed: ${err.message})`);
    throw new Error(`${table} destination write failed`);
  }
  console.log(`[mirror] ${table}: ${result.rows.length} row(s)`);
  return result.rows.length;
}

export async function runMarketMirror({
  src,
  dst,
  tables = {
    latestOne: LATEST_ONE,
    perKey: PER_KEY,
    history: HISTORY,
    purgedAccountTables: PURGED_ACCOUNT_TABLES,
  },
  maxAttempts = DEFAULT_MAX_ATTEMPTS,
  sleep = (delay) => new Promise((resolveDelay) => setTimeout(resolveDelay, delay)),
  log = (entry) => console.log(`[mirror] ${JSON.stringify(entry)}`),
  now = () => new Date().toISOString(),
  runId = `${Date.now()}-${process.pid}`,
}) {
  const latestOne = tables.latestOne ?? LATEST_ONE;
  const perKey = tables.perKey ?? PER_KEY;
  const history = tables.history ?? HISTORY;
  const purgedAccountTables = tables.purgedAccountTables ?? PURGED_ACCOUNT_TABLES;
  const opts = { maxAttempts, sleep, log, now, runId };

  // R-097: this ran OUTSIDE the retry ladder and outside `failures[]`, so a
  // documented transient 502 on the purge was console.warn'd and the mirror
  // proceeded, reported done and exited 0 — leaving account-derived rows from
  // the production book in the PUBLIC demo database indefinitely, with no
  // signal in the exit status, the health rows or the retry log. It is
  // retried like every other statement, and a purge that ultimately fails is
  // fatal BEFORE any snapshot row is written.
  for (const table of purgedAccountTables) {
    try {
      await retryOperation({
        phase: `${table}:account_purge`,
        operation: () => dst.execute(`DELETE FROM ${table}`),
        maxAttempts,
        sleep,
        log,
        now,
        runId,
      });
      console.log(`[mirror] purged account-derived table: ${table}`);
    } catch (err) {
      console.error(`[mirror] FATAL purge ${table} failed: ${err.message}`);
      throw new Error(
        `account-data purge failed for ${table}: ${err.message} — refusing to ` +
        `mirror while production account rows may remain in the demo database`,
      );
    }
  }

  let total = 0;
  const failures = [];
  for (const { table, orderCol } of latestOne) {
    try {
      total += await mirrorQuery(
        src, dst, table,
        `SELECT * FROM ${table} ORDER BY ${orderCol} DESC LIMIT 1`,
        `DELETE FROM ${table} WHERE ${orderCol} NOT IN (SELECT ${orderCol} FROM ${table} ORDER BY ${orderCol} DESC LIMIT 1)`,
        opts,
      );
    } catch { failures.push(table); }
  }
  for (const { table, key, orderCol } of perKey) {
    try {
      total += await mirrorQuery(
        src, dst, table,
        `SELECT * FROM (
           SELECT *, ROW_NUMBER() OVER (PARTITION BY ${key} ORDER BY ${orderCol} DESC) AS rn
           FROM ${table}
         ) WHERE rn = 1`,
        `DELETE FROM ${table} WHERE (${key}, ${orderCol}) NOT IN (` +
          `SELECT ${key}, MAX(${orderCol}) FROM ${table} GROUP BY ${key})`,
        opts,
      );
    } catch { failures.push(table); }
  }
  for (const { table, orderCol, limit } of history) {
    try {
      total += await mirrorQuery(
        src, dst, table,
        `SELECT * FROM ${table} ORDER BY ${orderCol} DESC LIMIT ${limit}`,
        `DELETE FROM ${table} WHERE ${orderCol} NOT IN (SELECT ${orderCol} FROM ${table} ORDER BY ${orderCol} DESC LIMIT ${limit})`,
        opts,
      );
    } catch { failures.push(table); }
  }
  if (failures.length) throw new Error(`required table failures: ${failures.join(", ")}`);
  console.log(`[mirror] done — ${total} row(s) mirrored`);
  return { total, failures };
}

async function main() {
  const srcUrl = process.env.TURSO_DB_URL;
  const srcToken = process.env.TURSO_AUTH_TOKEN;
  const dstUrl = process.env.TURSO_DEMO_DB_URL;
  const dstToken = process.env.TURSO_DEMO_AUTH_TOKEN;
  const maxAttempts = Number(
    process.env.MIRROR_MAX_ATTEMPTS || DEFAULT_MAX_ATTEMPTS,
  );

  if (!srcUrl || !srcToken) throw new Error("TURSO_DB_URL + TURSO_AUTH_TOKEN (prod source) required");
  if (!dstUrl || !dstToken) throw new Error("TURSO_DEMO_DB_URL + TURSO_DEMO_AUTH_TOKEN (demo dest) required");
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 5) {
    throw new Error("MIRROR_MAX_ATTEMPTS must be an integer from 1 to 5");
  }
  if (!dstUrl.includes(DEMO_MARKER)) {
    throw new Error(`dest ${dstUrl} is not the demo DB (missing '${DEMO_MARKER}') — refusing`);
  }
  if (dstUrl.replace(DEMO_MARKER, "").includes(PROD_MARKER) || dstUrl === srcUrl) {
    throw new Error("dest looks like prod / equals source — refusing to mirror");
  }

  const src = createClient({ url: toHttp(srcUrl), authToken: srcToken });
  const dst = createClient({ url: toHttp(dstUrl), authToken: dstToken });
  const startedAt = new Date().toISOString();
  try {
    await runMarketMirror({ src, dst, maxAttempts });
    await heartbeat("ok", { startedAt, finishedAt: new Date().toISOString() });
  } catch (err) {
    await heartbeat("error", {
      startedAt,
      finishedAt: new Date().toISOString(),
      error: { message: err?.message ?? String(err) },
    });
    throw err;
  } finally {
    src.close?.();
    dst.close?.();
  }
}

/** Telemetry must never mask the mirror's own outcome. */
async function heartbeat(state, extra) {
  try {
    await recordServiceHealth(SERVICE_NAME, state, extra);
  } catch (err) {
    console.error(`[mirror] service_health heartbeat failed: ${err?.message ?? err}`);
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  main().catch((err) => {
    console.error("[mirror] FAILED:", err.message);
    process.exitCode = 1;
  });
}
