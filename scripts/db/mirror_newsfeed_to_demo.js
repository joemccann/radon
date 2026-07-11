#!/usr/bin/env node
/**
 * Mirror recent public newsfeed `posts` from the PROD Turso into the DEMO Turso,
 * so demo.radon.run's "Live Market Analysis" panel shows real headlines.
 *
 * Isolation: this is a PROD-SIDE job — the only place that holds BOTH the prod
 * and demo Turso credentials. The demo deployment never holds prod creds; it
 * only reads its own `posts` table (getDb -> demo DB). Post images are public on
 * media.radon.run and are already absolute URLs in prod rows, so rows copy
 * across verbatim.
 *
 * Source: TURSO_DB_URL + TURSO_AUTH_TOKEN (prod).
 * Dest:   TURSO_DEMO_DB_URL + TURSO_DEMO_AUTH_TOKEN (demo).
 * Optional: MIRROR_POST_LIMIT (default 400).
 */
import { createClient } from "@libsql/client";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const PROD_MARKER = "radon-joemccann";
const DEMO_MARKER = "radon-demo";
const SERVICE_NAME = "demo-newsfeed-mirror";
const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_ATTEMPTS = 3;
const RETRY_DELAYS_MS = [500, 2_000];

// libsql:// / wss:// -> https:// so the client uses the stateless HTTP transport
// (no wedging WebSocket singleton). Mirrors web/lib/db.ts:toHttpTransportUrl.
export function toHttp(url) {
  if (!url) return url;
  if (url.startsWith("libsql://")) return `https://${url.slice("libsql://".length)}`;
  if (url.startsWith("wss://")) return `https://${url.slice("wss://".length)}`;
  if (url.startsWith("ws://")) return `http://${url.slice("ws://".length)}`;
  return url;
}

export function createBoundedFetch({ timeoutMs, fetchImpl = globalThis.fetch }) {
  return (input, init = {}) => {
    const timeoutSignal = AbortSignal.timeout(timeoutMs);
    const signal = init.signal
      ? AbortSignal.any([init.signal, timeoutSignal])
      : timeoutSignal;
    return fetchImpl(input, { ...init, signal });
  };
}

function isTransient(error) {
  const status = Number(error?.status ?? error?.statusCode ?? 0);
  const code = String(error?.code ?? "").toUpperCase();
  const message = String(error?.message ?? error).toLowerCase();
  return (
    error instanceof TypeError
    || [408, 425, 429].includes(status)
    || status >= 500
    || ["ECONNRESET", "ECONNREFUSED", "EAI_AGAIN", "ETIMEDOUT"].includes(code)
    || error?.name === "AbortError"
    || error?.name === "TimeoutError"
    || message.includes("fetch failed")
    || message.includes("timed out")
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
      const retrying = attempt < maxAttempts && isTransient(error);
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

async function recordHealth(src, state, detail, now) {
  const timestamp = now();
  await src.execute({
    sql: `INSERT INTO service_health
      (service, state, last_attempt_started_at, last_attempt_finished_at, last_error, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(service) DO UPDATE SET
        state = excluded.state,
        last_attempt_started_at = excluded.last_attempt_started_at,
        last_attempt_finished_at = excluded.last_attempt_finished_at,
        last_error = excluded.last_error,
        updated_at = excluded.updated_at`,
    args: [SERVICE_NAME, state, timestamp, timestamp, detail || null, timestamp],
  });
}

const UPSERT_SQL = `INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  ON CONFLICT(id) DO UPDATE SET
    title       = excluded.title,
    content     = excluded.content,
    timestamp   = excluded.timestamp,
    images      = excluded.images,
    raw_images  = excluded.raw_images,
    tags        = excluded.tags,
    tags_text   = excluded.tags_text,
    tags_vision = excluded.tags_vision,
    updated_at  = excluded.updated_at`;

export async function runNewsfeedMirror({
  src,
  dst,
  limit,
  maxAttempts = DEFAULT_MAX_ATTEMPTS,
  sleep = (delay) => new Promise((resolveDelay) => setTimeout(resolveDelay, delay)),
  log = (entry) => console.log(`[mirror] ${JSON.stringify(entry)}`),
  now = () => new Date().toISOString(),
  runId = `${Date.now()}-${process.pid}`,
}) {
  const attempt = (phase, operation) => retryOperation({
    phase,
    operation,
    maxAttempts,
    sleep,
    log,
    now,
    runId,
  });

  try {
    const { rows } = await attempt("source_read", () => src.execute({
      sql: `SELECT id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at
            FROM posts ORDER BY timestamp DESC LIMIT ?`,
      args: [limit],
    }));
    log({
      timestamp: now(),
      run_id: runId,
      phase: "source_read",
      event: "complete",
      rows: rows.length,
    });

    const batch = rows.map((r) => ({
      sql: UPSERT_SQL,
      args: [
        r.id, r.title, r.content ?? null, r.timestamp,
        r.images ?? "[]", r.raw_images ?? "[]",
        r.tags ?? "[]", r.tags_text ?? "[]", r.tags_vision ?? "[]",
        r.created_at, r.updated_at,
      ],
    }));
    if (batch.length) {
      await attempt("destination_write", () => dst.batch(batch, "write"));
    }

    const { rows: after } = await attempt(
      "destination_verify",
      () => dst.execute("SELECT count(*) AS n, max(timestamp) AS newest FROM posts"),
    );
    const total = Number(after[0]?.n ?? 0);
    log({
      timestamp: now(),
      run_id: runId,
      phase: "complete",
      event: "ok",
      mirrored: rows.length,
      total,
      newest: after[0]?.newest ?? null,
    });
    await recordHealth(src, "ok", `mirrored ${rows.length}; demo total ${total}`, now);
    return { mirrored: rows.length, total };
  } catch (error) {
    const detail = String(error?.message ?? error).slice(0, 500);
    try {
      await recordHealth(src, "error", detail, now);
    } catch (healthError) {
      log({
        timestamp: now(),
        run_id: runId,
        phase: "health_write",
        event: "failed",
        error: String(healthError?.message ?? healthError),
      });
    }
    throw error;
  }
}

async function main() {
  const srcUrl = process.env.TURSO_DB_URL;
  const srcToken = process.env.TURSO_AUTH_TOKEN;
  const dstUrl = process.env.TURSO_DEMO_DB_URL;
  const dstToken = process.env.TURSO_DEMO_AUTH_TOKEN;
  const limit = Number(process.env.MIRROR_POST_LIMIT || 400);
  const timeoutMs = Number(process.env.MIRROR_TIMEOUT_MS || DEFAULT_TIMEOUT_MS);
  const maxAttempts = Number(
    process.env.MIRROR_MAX_ATTEMPTS || DEFAULT_MAX_ATTEMPTS,
  );

  if (!srcUrl || !srcToken) throw new Error("TURSO_DB_URL + TURSO_AUTH_TOKEN (prod source) required");
  if (!dstUrl || !dstToken) throw new Error("TURSO_DEMO_DB_URL + TURSO_DEMO_AUTH_TOKEN (demo dest) required");
  if (!Number.isInteger(limit) || limit <= 0 || limit > 2_000) {
    throw new Error("MIRROR_POST_LIMIT must be an integer from 1 to 2000");
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1_000 || timeoutMs > 60_000) {
    throw new Error("MIRROR_TIMEOUT_MS must be an integer from 1000 to 60000");
  }
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 5) {
    throw new Error("MIRROR_MAX_ATTEMPTS must be an integer from 1 to 5");
  }

  // Isolation guards: dest must be the demo DB, never prod, never the same DB.
  if (!dstUrl.includes(DEMO_MARKER)) {
    throw new Error(`dest ${dstUrl} is not the demo DB (missing '${DEMO_MARKER}') — refusing`);
  }
  if (dstUrl.replace(DEMO_MARKER, "").includes(PROD_MARKER) || dstUrl === srcUrl) {
    throw new Error("dest looks like prod / equals source — refusing to mirror");
  }

  const boundedFetch = createBoundedFetch({ timeoutMs });
  const src = createClient({
    url: toHttp(srcUrl), authToken: srcToken, fetch: boundedFetch,
  });
  const dst = createClient({
    url: toHttp(dstUrl), authToken: dstToken, fetch: boundedFetch,
  });
  try {
    await runNewsfeedMirror({ src, dst, limit, maxAttempts });
  } finally {
    src.close();
    dst.close();
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  main().catch((err) => {
    console.error(`[mirror] ${JSON.stringify({
      timestamp: new Date().toISOString(),
      run_id: `${Date.now()}-${process.pid}`,
      phase: "main",
      event: "failed",
      error: String(err?.message ?? err),
    })}`);
    process.exitCode = 1;
  });
}
