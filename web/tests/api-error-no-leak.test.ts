import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Regression: Next.js user-write API error responses must NEVER echo secrets.
 *
 * Sibling to scripts/api/tests/test_no_secret_leakage.py — the FastAPI half of
 * the "/health leaked IB account IDs" incident class
 * (feedback_health_endpoint_public_leak_and_trust_chokepoint.md). On the
 * Next.js side the hazard is identical: profile / watchlist / bookmarks /
 * ratings all catch a DB or upstream error and pass the RAW `err.message`
 * straight into the response as `detail` (via jsonApiError, which serialises
 * `detail` into the JSON body). A libsql error message commonly contains the
 * Turso URL (`libsql://radon-…turso.io`) and the connection auth token, and an
 * upstream failure can carry an account id. Any of those riding out in the
 * `detail` field is a leak to the signed-in client.
 *
 * We drive each route's catch branch by mocking getDb()/radonFetch to throw an
 * error whose message CONTAINS a fake secret, then assert the response body
 * does NOT contain it.
 *
 * FINDING: these routes LEAK today (detail === err.message). Per the stream
 * brief we do NOT patch the routes here. We pin the leak with `it.fails`
 * (vitest strict-xfail): the body assertion below is exact and is NOT weakened.
 * `it.fails` passes only while the inner assertion FAILS — i.e. while the leak
 * exists. The moment a route is fixed to scrub the detail, the inner assertion
 * passes, `it.fails` flips to a failure, and whoever landed the fix must remove
 * the `.fails`. That keeps the invariant honest while the suite is green.
 */

// --- Fake secrets (not real; only need to look real for a verbatim-echo check)
const FAKE_TURSO_URL = "libsql://radon-leaktest.aws-us-west-2.turso.io";
const FAKE_AUTH_TOKEN = "auth_token=eyJSEKRETleaktoken1234567890";
const FAKE_ACCOUNT_ID = "U7654321";
const SECRET_BLOB =
  `LibsqlError: SERVER_ERROR: failed to connect to ${FAKE_TURSO_URL} ` +
  `with ${FAKE_AUTH_TOKEN}; account ${FAKE_ACCOUNT_ID} unauthorized`;
const SECRET_NEEDLES = [FAKE_TURSO_URL, FAKE_AUTH_TOKEN, FAKE_ACCOUNT_ID, "SEKRET"];

// --- Auth mock: always signed in so we reach the handler body / catch branch.
let currentUserId: string | null = "user_test_1";
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: currentUserId })),
}));

// --- DB mock: a client whose execute() throws the secret-bearing error.
const leakyExecute = vi.fn(async () => {
  throw new Error(SECRET_BLOB);
});
vi.mock("@/lib/db", () => ({
  getDb: vi.fn(() => ({ execute: leakyExecute })),
}));

// --- radonFetch mock (for the ratings route) throws the secret-bearing error.
vi.mock("@/lib/radonApi", () => ({
  radonFetch: vi.fn(async () => {
    throw new Error(SECRET_BLOB);
  }),
}));

function req(url: string, init?: RequestInit): Request {
  return new Request(url, init);
}

async function bodyTextOf(res: Response): Promise<string> {
  return res.text();
}

/**
 * Exact no-leak assertion. Reused by every case. NEVER weaken this — if a route
 * stops leaking, fix the test marker (drop `.fails`), not this assertion.
 */
async function expectNoSecretInBody(res: Response, route: string): Promise<void> {
  const text = await bodyTextOf(res);
  expect(res.status, `${route} should have entered its error branch`).toBeGreaterThanOrEqual(400);
  for (const needle of SECRET_NEEDLES) {
    expect(
      text.includes(needle),
      `SECRET LEAK on ${route}: response body echoed ${needle}. ` +
        `body=${text}. Scrub err.message before putting it in 'detail'.`,
    ).toBe(false);
  }
}

beforeEach(() => {
  vi.resetModules();
  currentUserId = "user_test_1";
  leakyExecute.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Next.js user-write routes — no secret leakage in error responses", () => {
  // Each case is `it.fails` = strict-xfail: passes ONLY while the route still
  // leaks. A fix that scrubs the detail flips it to a failure (remove .fails).

  it.fails("PUT /api/profile does not leak DB error secrets", async () => {
    const { PUT } = await import("../app/api/profile/route");
    const res = await PUT(
      req("http://localhost/api/profile", {
        method: "PUT",
        body: JSON.stringify({ username: "alice" }),
      }),
    );
    await expectNoSecretInBody(res, "PUT /api/profile");
  });

  it.fails("GET /api/profile does not leak DB error secrets", async () => {
    const { GET } = await import("../app/api/profile/route");
    const res = await GET();
    await expectNoSecretInBody(res, "GET /api/profile");
  });

  it.fails("POST /api/watchlist does not leak DB error secrets", async () => {
    const { POST } = await import("../app/api/watchlist/route");
    const res = await POST(
      req("http://localhost/api/watchlist", {
        method: "POST",
        body: JSON.stringify({ symbol: "AAPL" }),
      }),
    );
    await expectNoSecretInBody(res, "POST /api/watchlist");
  });

  it.fails("GET /api/watchlist does not leak DB error secrets", async () => {
    const { GET } = await import("../app/api/watchlist/route");
    const res = await GET();
    await expectNoSecretInBody(res, "GET /api/watchlist");
  });

  it.fails("POST /api/bookmarks does not leak DB error secrets", async () => {
    const { POST } = await import("../app/api/bookmarks/route");
    const res = await POST(
      req("http://localhost/api/bookmarks", {
        method: "POST",
        body: JSON.stringify({ post_id: "post_123" }),
      }),
    );
    await expectNoSecretInBody(res, "POST /api/bookmarks");
  });

  it.fails("GET /api/bookmarks does not leak DB error secrets", async () => {
    const { GET } = await import("../app/api/bookmarks/route");
    const res = await GET();
    await expectNoSecretInBody(res, "GET /api/bookmarks");
  });

  it.fails("GET /api/ticker/ratings does not leak upstream error secrets", async () => {
    const { GET } = await import("../app/api/ticker/ratings/route");
    const res = await GET(req("http://localhost/api/ticker/ratings?ticker=AAPL"));
    await expectNoSecretInBody(res, "GET /api/ticker/ratings");
  });
});
