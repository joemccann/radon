// libSQL client for the Radon Turso DB.
//
// All Next.js API routes and FastAPI Python schedulers read/write through
// this client. Embedded replicas were retired on 2026-05-20 after WAL
// conflicts between local Node readers and Python/direct-cloud writers.
// The safe default is direct-to-cloud; a replica requires explicit opt-in.
//
// One singleton per process. The schema is compiled to a no-op when
// TURSO_DB_URL is unset (tests can mock the module).
//
// ── Why reads wedge in bursts, and the self-heal (2026-06-28) ────────────
// In the Node runtime `@libsql/client` (v0.17.x) resolves BOTH `libsql://` and
// `https://` to an HTTP client (`preferHttp=true`); only `wss://` is a true
// WebSocket. So the long-lived Next.js process holds a cached HTTP client whose
// undici keep-alive POOL can go stale — half-open sockets after an idle/NAT/edge
// drop. undici then reuses a dead socket and the request hangs until its own
// ~300s body timeout. Nothing recreates the pool, so once it rots EVERY route
// sharing the singleton times out at its 3000ms caller-side deadline AT ONCE,
// until the process restarts. Production signature: portfolio/service-health/gex
// reads all timing out in bursts while a FRESH connection on the same host read
// in 64ms (the "SYNC FAILED / 1 DEGRADED" banner + stuck "SAMPLING" chart).
//
// The operative fix is `selfHealing()` + `resetDb()`: drop the cached client on
// any stalled/failed call so the next caller builds a fresh pool. The URL is
// also pinned to `https://` (`toHttpTransportUrl`) — belt-and-suspenders so the
// transport stays stateless HTTP even in an Edge/web runtime or if a future
// @libsql default flips `preferHttp`. Reads MUST go through `dbExecute()`
// (`./dbExecute`) so a stalled pool is bounded at 3s and healed, never left to
// undici's 300s ceiling. See `feedback_libsql_http_transport_no_wss_singleton`.

import { createClient, type Client } from "@libsql/client";
import { Agent, fetch as undiciFetch } from "undici";
import path from "node:path";
import {
  createDbOperationIdentity,
  currentDbOperationIdentity,
  markCurrentDbOperationPoolGeneration,
  runWithDbOperation,
  type DbOperationIdentity,
} from "./dbOperation";

let cached: Client | null = null;

// ── Bounded undici pool (2026-07-02 incident) ─────────────────────────────
// The long-lived Next.js process accumulated 59 ESTABLISHED sockets to Turso;
// Turso then stopped servicing NEW requests from that IP — fresh connections
// completed TCP+TLS but responses never arrived — so every DB-backed route hit
// its 3s deadline until `systemctl restart radon-nextjs` dropped the sockets
// (523 such bursts since Jun 26). Two structural fixes:
//   1. Every Turso HTTP request dispatches through ONE undici Agent with a
//      small per-origin connection cap, so the pool can never grow unbounded.
//   2. `resetDb()` DESTROYS that Agent (aborting its sockets), so the
//      timeout self-heal actually drops wedged connections instead of
//      orphaning them behind a fresh client.
export const DB_POOL_MAX_CONNECTIONS = 8;
// Idle sockets are reaped after this window instead of accumulating. Must stay
// ABOVE the 30s `dbKeepAlive` heartbeat interval so the heartbeat keeps one
// socket on the warm path (~12ms) between 60s polls.
export const DB_POOL_KEEP_ALIVE_TIMEOUT_MS = 40_000;
/** Hard transport deadline. Unlike a Promise.race timeout, this aborts undici
 * and releases the socket occupied by the libSQL request. */
export const DB_TRANSPORT_TIMEOUT_MS = 2_750;
// ── Destroy cooldown (2026-07-02 incident, second wave) ──────────────────
// Agent.destroy() aborts every in-flight request on the shared pool; each
// aborted request rejects TypeError('fetch failed') and its catch calls
// resetDb() again, destroying the freshly rebuilt Agent under the next wave
// of traffic (3s heartbeat + parallel SWR polls) — a self-sustaining destroy
// storm that only a process restart cleared. Rate-limiting the destroy HERE
// (the one chokepoint all six reset callers funnel through) lets a genuinely
// wedged pool be torn down while collateral failures inside the window leave
// the replacement Agent alive. Must exceed the 3s heartbeat so the metronome
// cannot re-trigger within the same storm.
export const DB_POOL_DESTROY_COOLDOWN_MS = 5_000;
// ── Failure-cluster gate (2026-07-03 diagnosis) ──────────────────────────
// The ~hourly reset triggers are genuine Turso tail-latency events, not
// socket rot: a 35-min external monitor on the VPS measured p95 582ms /
// max ~1s on FRESH connections (the tail exists off-pool), and the
// watchdog's Python canary reads Turso fine in the same minute the app
// times out. An isolated >3s read is a slow query — destroying the Agent
// for it aborted 5-18 healthy concurrent reads every time. A genuine wedge
// fails EVERY read, so a second reset lands within seconds; only then is
// teardown warranted.
export const DB_POOL_FAILURE_CLUSTER_WINDOW_MS = 10_000;

let cachedAgent: Agent | null = null;
let lastAgentDestroyAtMs = Number.NEGATIVE_INFINITY;
let lastResetAttemptAtMs = Number.NEGATIVE_INFINITY;
let nextPoolGeneration = 0;
let activePoolGeneration: number | null = null;
let observedFailures = new WeakSet<DbOperationIdentity>();

function getPoolAgent(): Agent {
  if (!cachedAgent) {
    cachedAgent = new Agent({
      connections: DB_POOL_MAX_CONNECTIONS,
      keepAliveTimeout: DB_POOL_KEEP_ALIVE_TIMEOUT_MS,
    });
    nextPoolGeneration += 1;
    activePoolGeneration = nextPoolGeneration;
  }
  return cachedAgent;
}

/** Abort every socket in the bounded pool. Fire-and-forget: the next request
 * lazily builds a fresh Agent. Shared by the primary and demo clients — a
 * reset on either aborts both origins' sockets, which is acceptable (the
 * other side re-handshakes once) and keeps one teardown path.
 *
 * Twice gated, and on a skip the current Agent stays in place — nulling
 * without destroying would leak the old pool's sockets (the original
 * per-IP exhaustion disease):
 *   1. Cluster gate: only a SECOND failure inside the cluster window
 *      destroys — an isolated failure is Turso tail latency, not a wedge.
 *   2. Cooldown: destroying again within the window is the storm.
 * `force` is the test seam. */
function destroyPoolAgent(force = false): void {
  const now = Date.now();
  if (!force) {
    const clustered = now - lastResetAttemptAtMs <= DB_POOL_FAILURE_CLUSTER_WINDOW_MS;
    lastResetAttemptAtMs = now;
    if (!clustered) return;
    if (now - lastAgentDestroyAtMs < DB_POOL_DESTROY_COOLDOWN_MS) return;
  }
  lastAgentDestroyAtMs = now;
  const agent = cachedAgent;
  cachedAgent = null;
  activePoolGeneration = null;
  agent?.destroy().catch(() => {});
}

function transportAbort(signals: Array<AbortSignal | null | undefined>): {
  signal: AbortSignal;
  cleanup: () => void;
} {
  const controller = new AbortController();
  const listeners: Array<{ signal: AbortSignal; listener: () => void }> = [];

  for (const signal of signals) {
    if (!signal) continue;
    if (signal.aborted) {
      controller.abort(signal.reason);
      break;
    }
    const listener = () => controller.abort(signal.reason);
    signal.addEventListener("abort", listener, { once: true });
    listeners.push({ signal, listener });
  }

  const timer = setTimeout(() => {
    const error = new Error(
      `Turso HTTP transport timed out after ${DB_TRANSPORT_TIMEOUT_MS}ms`,
    );
    error.name = "TimeoutError";
    controller.abort(error);
  }, DB_TRANSPORT_TIMEOUT_MS);
  timer.unref?.();

  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timer);
      for (const { signal, listener } of listeners) {
        signal.removeEventListener("abort", listener);
      }
    },
  };
}

/** `fetch` pinned to the bounded pool — passed to `createClient` so every
 * Turso HTTP request the app makes goes through the capped Agent.
 *
 * Uses undici's OWN fetch (not `globalThis.fetch`): Node's bundled fetch
 * rejects an Agent from a different undici major ("invalid onRequestStart
 * method"), so same-realm dispatch is the only version-proof pairing. The
 * hrana client passes global `Request` objects, which undici's fetch
 * brand-checks and refuses — translate them to (url, init). hrana bodies are
 * strings/bytes; the translation preserves any caller signal and composes it
 * with the hard transport deadline below. */
export async function pooledDbFetch(
  input: string | URL | Request,
  init?: RequestInit,
): Promise<Response> {
  let target: string | URL;
  let requestInit: RequestInit;
  let requestSignal: AbortSignal | undefined;

  if (typeof input === "object" && input !== null && "url" in input) {
    const request = input as Request;
    const body =
      request.method === "GET" || request.method === "HEAD"
        ? undefined
        : new Uint8Array(await request.arrayBuffer());
    target = request.url;
    requestSignal = request.signal;
    requestInit = {
      method: request.method,
      headers: request.headers,
      body,
      ...init,
    };
  } else {
    target = input as string | URL;
    requestInit = { ...init };
  }

  // Resolve the dispatcher after Request body materialization. A reset in that
  // microtask gap must not route work onto an already-destroyed Agent.
  const dispatcher = getPoolAgent();
  if (activePoolGeneration !== null) {
    markCurrentDbOperationPoolGeneration(activePoolGeneration);
  }
  const abort = transportAbort([requestSignal, init?.signal]);
  try {
    return (await undiciFetch(target, {
      ...requestInit,
      dispatcher,
      signal: abort.signal,
    } as Parameters<typeof undiciFetch>[1])) as unknown as Response;
  } finally {
    abort.cleanup();
  }
}

// A stalled HTTP request still has to settle before its pooled socket frees up.
// If a call has not resolved within this ceiling we proactively drop the cached
// client so the NEXT caller rebuilds a fresh pool rather than queueing behind
// the stalled one. Larger than every caller-side read timeout (hot reads use
// 3000ms) so it only fires on a genuine stall.
const STALL_CEILING_MS = DB_TRANSPORT_TIMEOUT_MS;

/** Rewrite a Turso URL onto the stateless HTTP transport.
 *
 * `libsql://`/`wss://` → `https://`, `ws://` → `http://`. `https://`,
 * `http://`, and `file:` URLs pass through untouched. In Node `libsql://`
 * already resolves to HTTP; this guarantees it regardless of runtime. */
export function toHttpTransportUrl(url: string): string {
  if (url.startsWith("libsql://")) return `https://${url.slice("libsql://".length)}`;
  if (url.startsWith("wss://")) return `https://${url.slice("wss://".length)}`;
  if (url.startsWith("ws://")) return `http://${url.slice("ws://".length)}`;
  return url;
}

/** Per-origin snapshot of the bounded Agent's connection stats
 * (connected/free/pending/queued/running/size), or null before any pooled
 * request. Logged by dbExecute on failure: queued>0 at a read timeout means
 * the 8-conn cap is saturating; an idle pool points at a stale socket or a
 * blocked event loop instead. */
export function getPoolStats(): Record<string, unknown> | null {
  if (!cachedAgent) return null;
  const stats = (cachedAgent as { stats?: Record<string, unknown> }).stats;
  if (!stats || Object.keys(stats).length === 0) return null;
  return JSON.parse(JSON.stringify(stats));
}

/** Drop the cached primary client AND destroy the bounded undici pool so the
 * next getDb() opens fresh sockets.
 *
 * Public so the bounded helpers can recover promptly from a read timeout
 * instead of waiting for the in-flight request to hit the transport ceiling.
 * Destroying the Agent is the load-bearing half: without it a wedged pool's
 * sockets survive the client swap and keep pinning Turso's per-IP limit.
 * Idempotent. */
function isCurrentFailure(identity: DbOperationIdentity): boolean {
  if (observedFailures.has(identity)) return false;
  observedFailures.add(identity);

  return (
    identity.poolGeneration === null ||
    identity.poolGeneration === activePoolGeneration
  );
}

export function resetDb(identity?: DbOperationIdentity): void {
  if (identity && !isCurrentFailure(identity)) return;
  cached = null;
  destroyPoolAgent();
}

/** Drop the cached demo client so the next getDemoDb() opens a fresh pool. */
export function resetDemoDb(identity?: DbOperationIdentity): void {
  if (identity && !isCurrentFailure(identity)) return;
  cachedDemo = null;
  destroyPoolAgent();
}

/** Wrap a client so any failed or stalled `execute`/`batch` invokes `onHeal`,
 * which drops the cached singleton. Defence-in-depth: a client that lands in a
 * bad state (DNS, rotated token, stale undici pool) self-heals on the next call
 * instead of poisoning the process. `onHeal` is passed in (rather than closing
 * over `cached`) so the same wrapper protects the primary AND the demo client,
 * each resetting its own cache slot. */
function selfHealing(
  client: Client,
  onHeal: (identity: DbOperationIdentity) => void,
): Client {
  const healOn = (
    settled: Promise<unknown>,
    identity: DbOperationIdentity,
  ): void => {
    const timer = setTimeout(() => onHeal(identity), STALL_CEILING_MS);
    timer.unref?.();
    settled.then(
      () => clearTimeout(timer),
      () => {
        clearTimeout(timer);
        onHeal(identity);
      },
    );
  };

  return new Proxy(client, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (prop === "execute" || prop === "batch") {
        return (...args: unknown[]) => {
          const identity =
            currentDbOperationIdentity() ?? createDbOperationIdentity();
          let result: unknown;
          try {
            result = runWithDbOperation(identity, () =>
              (value as (...a: unknown[]) => unknown).apply(target, args),
            );
          } catch (error) {
            onHeal(identity);
            throw error;
          }
          if (result && typeof (result as Promise<unknown>).then === "function") {
            healOn(result as Promise<unknown>, identity);
          }
          return result;
        };
      }
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

function projectRoot(): string {
  // Resolve relative to the calling Next.js process. `process.cwd()` is
  // the `web/` dir under `npm run dev`; in `app-radon-run` Docker it's
  // `/app`. Either way, putting the replica next to `data/` matches the
  // repo layout and keeps the file out of git. Replica mode is opt-in; keep
  // its host path opaque so Turbopack does not package the entire data tree.
  return path.resolve(/* turbopackIgnore: true */ process.cwd(), "..", "data");
}

export function getDb(): Client {
  if (cached) return cached;

  const url = process.env.TURSO_DB_URL;
  const authToken = process.env.TURSO_AUTH_TOKEN;

  if (!url) {
    throw new Error(
      "getDb: TURSO_DB_URL is not set. Add it to web/.env. See plan §0.",
    );
  }

  const useReplica = (
    process.env.NODE_ENV !== "test" &&
    process.env.RADON_DB_USE_REPLICA === "1" &&
    process.env.RADON_DB_NO_REPLICA !== "1"
  );

  const raw = createClient(
    useReplica
      ? {
          url: `file:${path.join(projectRoot(), "replica.db")}`,
          syncUrl: toHttpTransportUrl(url),
          authToken,
          syncInterval: 60, // background pull every 60s; writes are pushed immediately
          fetch: pooledDbFetch,
        }
      : { url: toHttpTransportUrl(url), authToken, fetch: pooledDbFetch },
  );

  // Heal through resetDb (not a bare `cached = null`) so the wedged pool's
  // sockets are destroyed along with the client.
  cached = selfHealing(raw, resetDb);
  return cached;
}

let cachedDemo: Client | null = null;

/**
 * libSQL client for the SEPARATE demo Turso DB (demo.radon.run).
 *
 * Reads `TURSO_DEMO_DB_URL` / `TURSO_DEMO_AUTH_TOKEN` — never the prod
 * `TURSO_DB_URL`. The whole point of the demo environment is physical data
 * isolation (plan §Isolation): the operator's real positions live in a
 * different database that is simply absent here, so a forgotten WHERE clause
 * can never leak them. Direct-to-cloud only; demo has no replica.
 *
 * Matches the env names already consumed by `scripts/db/demo_seed.py` and
 * `scripts/ci/check_demo_isolation.py`.
 */
export function getDemoDb(): Client {
  if (cachedDemo) return cachedDemo;

  const url = process.env.TURSO_DEMO_DB_URL;
  const authToken = process.env.TURSO_DEMO_AUTH_TOKEN;

  if (!url) {
    throw new Error(
      "getDemoDb: TURSO_DEMO_DB_URL is not set. This is the SEPARATE demo DB, " +
        "not the prod TURSO_DB_URL. See docs/demo-environment.md §Isolation.",
    );
  }

  cachedDemo = selfHealing(
    createClient({ url: toHttpTransportUrl(url), authToken, fetch: pooledDbFetch }),
    resetDemoDb,
  );
  return cachedDemo;
}

// Legacy convenience for explicit replica opt-in callers.
export async function syncDb(): Promise<void> {
  const db = getDb();
  if ("sync" in db && typeof (db as { sync?: () => Promise<unknown> }).sync === "function") {
    await (db as { sync: () => Promise<unknown> }).sync();
  }
}

// Test seam — drop the cached clients + pool between vitest tests.
// Bypasses AND rearms both destroy gates so each test starts deterministic.
export function __resetDbForTests(): void {
  cached = null;
  cachedDemo = null;
  destroyPoolAgent(true);
  lastAgentDestroyAtMs = Number.NEGATIVE_INFINITY;
  lastResetAttemptAtMs = Number.NEGATIVE_INFINITY;
  nextPoolGeneration = 0;
  activePoolGeneration = null;
  observedFailures = new WeakSet<DbOperationIdentity>();
}

// Test seam — inject a libSQL client (typically in-memory) so route
// handlers can be tested hermetically.
export function __setDbForTests(client: Client): void {
  cached = client;
}

// Test seam — inject the demo libSQL client.
export function __setDemoDbForTests(client: Client): void {
  cachedDemo = client;
}
