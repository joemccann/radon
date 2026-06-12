// Shared libSQL writer for Node scrapers (newsfeed, future schedulers).
// Mirrors web/lib/db.ts but constructed for use from arbitrary Node scripts
// — the scraper runs outside Next.js and needs its own client instance.

import path from "node:path";
import { fileURLToPath } from "node:url";
import { createClient } from "@libsql/client";

const __filename = fileURLToPath(import.meta.url);
const PROJECT_ROOT = path.resolve(path.dirname(__filename), "..", "..");
const REPLICA_PATH = path.join(PROJECT_ROOT, "data", "replica.db");

let cached = null;

const MEDIA_ORIGIN = "https://media.radon.run";

// --- DUR-09: protocol-level bounds ----------------------------------------
// Turso "slow" is worse than Turso "down": an unbounded write can hang a
// scraper cycle forever. Three layers: (1) a per-request fetch timeout via
// AbortSignal (the direct-cloud URL is rewritten libsql:// → https:// so the
// HTTP client — the only transport that honours a custom fetch — is used),
// (2) 2 retries with backoff on transport errors only, (3) an in-process
// circuit breaker that fails fast after N consecutive failed operations and
// probes again after a cooldown. Circuit transitions log loudly.

const DEFAULT_DB_BOUNDS = Object.freeze({
  requestTimeoutMs: 10_000,
  retryBackoffMs: [500, 1500],
  circuitOpenThreshold: 5,
  circuitCooldownMs: 60_000,
});

let bounds = { ...DEFAULT_DB_BOUNDS };
const circuit = { consecutiveFailures: 0, openedAtMs: null };

export class DbCircuitOpenError extends Error {
  constructor(message) {
    super(message);
    this.name = "DbCircuitOpenError";
  }
}

function httpUrlFromLibsql(url) {
  if (url.startsWith("libsql://")) return `https://${url.slice("libsql://".length)}`;
  if (url.startsWith("wss://")) return `https://${url.slice("wss://".length)}`;
  return url;
}

function boundedFetch(input, init = {}) {
  return globalThis.fetch(input, {
    ...init,
    signal: AbortSignal.timeout(bounds.requestTimeoutMs),
  });
}

// Transport failures (retryable, count toward the circuit) vs statement
// failures (constraint/SQL errors — rethrown immediately, circuit untouched).
function isTransportError(err) {
  if (!err) return false;
  if (err.name === "AbortError" || err.name === "TimeoutError") return true;
  if (typeof err.code === "string" && /^(HRANA_|SERVER_|WEBSOCKET_)/.test(err.code)) return true;
  const message = String(err.message ?? "");
  if (/fetch failed|timed? ?out|timeout|aborted|network|socket|ECONN|ETIMEDOUT|EAI_AGAIN|EPIPE/i.test(message)) {
    return true;
  }
  return err.cause ? isTransportError(err.cause) : false;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function assertCircuitAllowsRequest(label) {
  if (circuit.openedAtMs === null) return;
  const elapsed = Date.now() - circuit.openedAtMs;
  if (elapsed < bounds.circuitCooldownMs) {
    const secsLeft = Math.ceil((bounds.circuitCooldownMs - elapsed) / 1000);
    throw new DbCircuitOpenError(
      `[radon-db] circuit OPEN (${label}): ${circuit.consecutiveFailures} consecutive ` +
        `transport failures against Turso; failing fast, next probe in ~${secsLeft}s`,
    );
  }
  console.error(`[radon-db] circuit HALF-OPEN (${label}): cooldown elapsed, probing Turso`);
}

function recordOperationSuccess(label) {
  if (circuit.consecutiveFailures > 0 || circuit.openedAtMs !== null) {
    console.error(`[radon-db] circuit CLOSED (${label}): Turso recovered`);
  }
  circuit.consecutiveFailures = 0;
  circuit.openedAtMs = null;
}

function recordOperationFailure(label, err) {
  circuit.consecutiveFailures += 1;
  const wasOpen = circuit.openedAtMs !== null;
  if (wasOpen || circuit.consecutiveFailures >= bounds.circuitOpenThreshold) {
    circuit.openedAtMs = Date.now();
    console.error(
      `[radon-db] circuit OPEN (${label}): ${circuit.consecutiveFailures} consecutive ` +
        `transport failures (last: ${err.message}); failing fast for ` +
        `${Math.round(bounds.circuitCooldownMs / 1000)}s`,
    );
  }
}

// Every Turso operation goes through here: circuit check, then up to
// 1 + retryBackoffMs.length attempts on transport errors.
async function withDbBounds(label, run) {
  assertCircuitAllowsRequest(label);
  let lastErr;
  for (let attempt = 0; attempt <= bounds.retryBackoffMs.length; attempt++) {
    try {
      const result = await run();
      recordOperationSuccess(label);
      return result;
    } catch (err) {
      if (!isTransportError(err)) throw err;
      lastErr = err;
      if (attempt < bounds.retryBackoffMs.length) {
        const backoff = bounds.retryBackoffMs[attempt];
        console.warn(
          `[radon-db] ${label} transport error (attempt ${attempt + 1}/` +
            `${bounds.retryBackoffMs.length + 1}): ${err.message}; retrying in ${backoff}ms`,
        );
        await sleep(backoff);
      }
    }
  }
  recordOperationFailure(label, lastErr);
  throw lastErr;
}
// ---------------------------------------------------------------------------

// posts.json stores relative `/media/<file>.png` paths because the local dev
// dashboard serves them out of web/public/media/. The Turso row is shared
// with app.radon.run (Hetzner), which has no local web/public — so we
// promote them to absolute URLs that resolve identically on both peers.
function absolutizeMedia(images) {
  if (!Array.isArray(images)) return [];
  return images.map((src) => {
    if (typeof src !== "string") return src;
    if (src.startsWith("https://") || src.startsWith("http://")) return src;
    // Caddy roots media.radon.run at /home/radon/radon-cloud/media/ so we
    // strip the leading /media/ — the rest of the path is the bare filename.
    if (src.startsWith("/media/")) return `${MEDIA_ORIGIN}/${src.slice("/media/".length)}`;
    return src;
  });
}

// Embedded replicas were retired on 2026-05-20 after WAL conflicts between
// multi-writer hosts (feedback_libsql_replica_one_writer.md). Mirrors
// web/lib/db.ts: direct-to-cloud is the default; a replica requires an
// explicit RADON_DB_USE_REPLICA=1 opt-in, and the legacy
// RADON_DB_NO_REPLICA=1 kill switch still forces the direct path.
function shouldUseReplica() {
  return (
    process.env.NODE_ENV !== "test" &&
    process.env.RADON_DB_USE_REPLICA === "1" &&
    process.env.RADON_DB_NO_REPLICA !== "1"
  );
}

export function resolveClientConfig() {
  const url = process.env.TURSO_DB_URL;
  const authToken = process.env.TURSO_AUTH_TOKEN;
  if (!url) {
    throw new Error(
      "scripts/db/writer.getDb: TURSO_DB_URL is unset. Load web/.env or root .env first.",
    );
  }

  // Direct-cloud goes over the libSQL HTTP transport (libsql:// would pick
  // hrana-over-websocket, which ignores a custom fetch) so the per-request
  // AbortSignal timeout is a real socket-level bound.
  if (!shouldUseReplica()) {
    return { url: httpUrlFromLibsql(url), authToken, fetch: boundedFetch };
  }

  console.warn(
    `[radon-db] WARNING: RADON_DB_USE_REPLICA=1 — opening the RETIRED libsql ` +
      `embedded replica at ${REPLICA_PATH}. Only one process per host may hold ` +
      `it (WalConflict). Direct-to-cloud has been the default since 2026-05-20.`,
  );
  return { url: `file:${REPLICA_PATH}`, syncUrl: url, authToken, syncInterval: 60 };
}

export function getDb() {
  if (cached) return cached;
  cached = createClient(resolveClientConfig());
  return cached;
}

// Idempotent — same id reapplies the row. updated_at always advances so the
// dashboard can reorder by recency if needed.
export async function upsertPost(post) {
  const db = getDb();
  const now = new Date().toISOString();
  await withDbBounds("upsertPost", () => db.execute({
    sql: `INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at)
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
            updated_at  = excluded.updated_at`,
    args: [
      post.id,
      post.title,
      post.content ?? null,
      post.timestamp,
      JSON.stringify(absolutizeMedia(post.images ?? [])),
      JSON.stringify(post.rawImages ?? []),
      JSON.stringify(post.tags ?? []),
      JSON.stringify(post.tags_text ?? []),
      JSON.stringify(post.tags_vision ?? []),
      post.createdAt ?? now,
      post.updatedAt ?? now,
    ],
  }));
}

export async function upsertPosts(posts) {
  if (!posts || posts.length === 0) return;
  const db = getDb();
  const now = new Date().toISOString();

  // Batch in chunks so a few hundred posts don't blow up the WAL frame.
  const CHUNK = 50;
  for (let i = 0; i < posts.length; i += CHUNK) {
    const slice = posts.slice(i, i + CHUNK);
    const stmts = slice.map((post) => ({
      sql: `INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at)
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
              updated_at  = excluded.updated_at`,
      args: [
        post.id,
        post.title,
        post.content ?? null,
        post.timestamp,
        JSON.stringify(absolutizeMedia(post.images ?? [])),
        JSON.stringify(post.rawImages ?? []),
        JSON.stringify(post.tags ?? []),
        JSON.stringify(post.tags_text ?? []),
        JSON.stringify(post.tags_vision ?? []),
        post.createdAt ?? now,
        post.updatedAt ?? now,
      ],
    }));
    await withDbBounds("upsertPosts", () => db.batch(stmts, "write"));
  }
}

// COLLATE NOCASE on the PK makes BTC/btc/Btc collide. INSERT OR IGNORE
// preserves the first canonical form seen.
export async function appendTaxonomy(tags) {
  if (!tags || tags.length === 0) return 0;
  const db = getDb();
  const now = new Date().toISOString();
  let added = 0;
  for (const tag of tags) {
    const result = await withDbBounds("appendTaxonomy", () => db.execute({
      sql: `INSERT OR IGNORE INTO tag_taxonomy (tag, created_at) VALUES (?, ?)`,
      args: [tag, now],
    }));
    if (result.rowsAffected > 0) added += 1;
  }
  return added;
}

export async function recordServiceHealth(service, state, extra = {}) {
  const db = getDb();
  const now = new Date().toISOString();
  await withDbBounds("recordServiceHealth", () => db.execute({
    sql: `INSERT INTO service_health (service, state, last_attempt_started_at, last_attempt_finished_at, last_error, updated_at)
          VALUES (?, ?, ?, ?, ?, ?)
          ON CONFLICT(service) DO UPDATE SET
            state                    = excluded.state,
            last_attempt_started_at  = COALESCE(excluded.last_attempt_started_at, service_health.last_attempt_started_at),
            last_attempt_finished_at = COALESCE(excluded.last_attempt_finished_at, service_health.last_attempt_finished_at),
            last_error               = excluded.last_error,
            updated_at               = excluded.updated_at`,
    args: [
      service,
      state,
      extra.startedAt ?? null,
      extra.finishedAt ?? null,
      extra.error ? JSON.stringify(extra.error) : null,
      now,
    ],
  }));
}

export function __resetDbForTests() {
  cached = null;
  bounds = { ...DEFAULT_DB_BOUNDS };
  circuit.consecutiveFailures = 0;
  circuit.openedAtMs = null;
}

// Test seam — shrink the retry/circuit knobs so breaker tests don't need
// minutes of fake-timer advancement.
export function __setDbBoundsForTests(overrides) {
  bounds = { ...bounds, ...overrides };
}

// Test seam — inject a libSQL client (typically in-memory) so tests can
// exercise the upsert/append helpers without a real Turso connection.
export function __setDbForTests(client) {
  cached = client;
}
