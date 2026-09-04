/**
 * Setup API error-code contract, asserted ON THE WIRE.
 *
 * Each first-run setup route is driven into every error state it can emit and
 * the response is checked as a client sees it: exact path, method, HTTP
 * status, and the parsed body's `code` / `error` / `requestId` fields. The
 * previous version of this file regex-scraped `code: "..."` literals out of
 * the route sources, which could not see a code emitted through a helper
 * (`setupTokenRejection` emits SETUP_TOKEN_INVALID and RATE_LIMITED from
 * lib/setup/setupToken.ts) and reded on reformatting rather than on behavior.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readdirSync, statSync } from "node:fs";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

const webRoot = path.resolve(__dirname, "..");

/** Route modules keyed by the exact request path they serve. */
const SETUP_ROUTES: Record<string, () => Promise<Record<string, unknown>>> = {
  "/api/setup/status": () => import("../app/api/setup/status/route"),
  "/api/setup/validate": () => import("../app/api/setup/validate/route"),
  "/api/setup/complete": () => import("../app/api/setup/complete/route"),
};

/** Paths actually driven by a test, for the coverage assertion at the end. */
const exercised = new Set<string>();

async function callSetupRoute(pathname: string, body: unknown): Promise<Response> {
  const load = SETUP_ROUTES[pathname];
  expect(load, `no route registered for ${pathname}`).toBeTypeOf("function");
  const mod = (await load()) as {
    POST?: (request: Request) => Promise<Response>;
    GET?: unknown;
  };
  // The wire contract includes the method: these surfaces are POST-only so the
  // setup token travels in the body and never lands in an access log.
  expect(typeof mod.POST, `${pathname} must export POST`).toBe("function");
  expect(mod.GET, `${pathname} must not expose GET`).toBeUndefined();
  exercised.add(pathname);
  return mod.POST!(
    new Request(`http://localhost:3000${pathname}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

async function expectWireError(
  pathname: string,
  body: unknown,
  expected: { status: number; code: string },
): Promise<Record<string, unknown>> {
  const res = await callSetupRoute(pathname, body);
  expect(res.status, `${pathname} status`).toBe(expected.status);
  const json = (await res.json()) as Record<string, unknown>;
  expect(json.code, `${pathname} wire code`).toBe(expected.code);
  expect(typeof json.error, `${pathname} error message`).toBe("string");
  expect(json.error, `${pathname} error message`).not.toBe("");
  expect(typeof json.requestId, `${pathname} requestId`).toBe("string");
  return json;
}

const ENV_KEYS = [
  "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
  "CLERK_SECRET_KEY",
  "RADON_SETUP_COMPLETE",
  "RADON_SETUP_TOKEN",
] as const;

let savedEnv: Record<string, string | undefined> = {};

// Shape of FastAPI GET /credentials (scripts/api/routes/credentials.py).
const FASTAPI_REGISTRY = {
  services: [
    { id: "clerk", fields: [{ name: "CLERK_SECRET_KEY" }] },
  ],
};

function mockRadonApi(fetchImpl: (apiPath: string, init?: { method?: string }) => unknown): void {
  vi.doMock("@/lib/radonApi", () => ({
    radonFetch: vi.fn(async (apiPath: string, init?: { method?: string }) =>
      fetchImpl(apiPath, init),
    ),
    RadonApiError: class RadonApiError extends Error {
      status = 500;
      detail: unknown = null;
    },
    radonErrorDetailText: () => "",
  }));
}

beforeEach(() => {
  savedEnv = {};
  for (const key of ENV_KEYS) {
    savedEnv[key] = process.env[key];
    delete process.env[key];
  }
  // Setup mode is "no Clerk keys and not yet complete"; the token is fixed so
  // a test can present a right or a wrong one deliberately.
  process.env.RADON_SETUP_TOKEN = "tok-1";
});

afterEach(async () => {
  vi.restoreAllMocks();
  vi.doUnmock("@/lib/radonApi");
  const tokenModule = await import("../lib/setup/setupToken");
  tokenModule.__resetSetupTokenForTests();
  vi.resetModules();
  for (const [key, value] of Object.entries(savedEnv)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

describe("setup routes: token gate on the wire", () => {
  for (const pathname of Object.keys(SETUP_ROUTES)) {
    it(`${pathname} rejects a wrong token with 401 SETUP_TOKEN_INVALID`, async () => {
      await expectWireError(
        pathname,
        { token: "wrong", service: "clerk", services: { clerk: { CLERK_SECRET_KEY: "x" } } },
        { status: 401, code: "SETUP_TOKEN_INVALID" },
      );
    });

    it(`${pathname} rejects a missing token with 401 SETUP_TOKEN_INVALID`, async () => {
      await expectWireError(
        pathname,
        { service: "clerk", services: { clerk: { CLERK_SECRET_KEY: "x" } } },
        { status: 401, code: "SETUP_TOKEN_INVALID" },
      );
    });

    it(`${pathname} answers 429 RATE_LIMITED once the wrong-token budget is spent`, async () => {
      for (let i = 0; i < 10; i += 1) {
        const res = await callSetupRoute(pathname, { token: "wrong" });
        expect(res.status).toBe(401);
      }
      const json = await expectWireError(
        pathname,
        { token: "wrong" },
        { status: 429, code: "RATE_LIMITED" },
      );
      expect(json.code).toBe("RATE_LIMITED");
    });
  }
});

describe("setup routes: mode gates on the wire", () => {
  for (const pathname of Object.keys(SETUP_ROUTES)) {
    it(`${pathname} is 404 NOT_FOUND once Clerk keys exist`, async () => {
      process.env.CLERK_SECRET_KEY = "sk_live_x";
      await expectWireError(
        pathname,
        { token: "tok-1", service: "clerk", services: { clerk: { CLERK_SECRET_KEY: "x" } } },
        { status: 404, code: "NOT_FOUND" },
      );
    });

    it(`${pathname} is 403 SETUP_ALREADY_COMPLETE when setup latched but auth is unloaded`, async () => {
      process.env.RADON_SETUP_COMPLETE = "1";
      await expectWireError(
        pathname,
        { token: "tok-1", service: "clerk", services: { clerk: { CLERK_SECRET_KEY: "x" } } },
        { status: 403, code: "SETUP_ALREADY_COMPLETE" },
      );
    });
  }
});

describe("setup routes: request-shape and upstream failures on the wire", () => {
  it("/api/setup/validate rejects a missing service with 400 BAD_REQUEST", async () => {
    await expectWireError(
      "/api/setup/validate",
      { token: "tok-1", values: { CLERK_SECRET_KEY: "x" } },
      { status: 400, code: "BAD_REQUEST" },
    );
  });

  it("/api/setup/validate rejects a malformed service id with 400 BAD_REQUEST", async () => {
    await expectWireError(
      "/api/setup/validate",
      { token: "tok-1", service: "Not A Service" },
      { status: 400, code: "BAD_REQUEST" },
    );
  });

  it("/api/setup/validate reports an unreachable FastAPI as 502 BACKEND_UNAVAILABLE", async () => {
    mockRadonApi(() => {
      throw new Error("ECONNREFUSED");
    });
    await expectWireError(
      "/api/setup/validate",
      { token: "tok-1", service: "clerk", values: { CLERK_SECRET_KEY: "x" } },
      { status: 502, code: "BACKEND_UNAVAILABLE" },
    );
  });

  it("/api/setup/complete rejects an empty services object with 400 BAD_REQUEST", async () => {
    await expectWireError(
      "/api/setup/complete",
      { token: "tok-1", services: {} },
      { status: 400, code: "BAD_REQUEST" },
    );
  });

  it("/api/setup/complete rejects an unknown field with 400 BAD_REQUEST", async () => {
    mockRadonApi((_p, init) =>
      init?.method === "GET" ? FASTAPI_REGISTRY : { validation: { status: "valid", message: "" } },
    );
    await expectWireError(
      "/api/setup/complete",
      { token: "tok-1", services: { clerk: { NOT_A_FIELD: "x" } } },
      { status: 400, code: "BAD_REQUEST" },
    );
  });

  it("/api/setup/complete is 500 SETUP_REPO_ROOT_INVALID when cwd is not the monorepo", async () => {
    // A bare temp dir: no package.json, no web/package.json, so resolveRepoRoot
    // walks to the filesystem root and returns null.
    const stray = await fs.mkdtemp(path.join(os.tmpdir(), "radon-not-a-repo-"));
    vi.spyOn(process, "cwd").mockReturnValue(stray);
    mockRadonApi((_p, init) =>
      init?.method === "GET" ? FASTAPI_REGISTRY : { validation: { status: "valid", message: "" } },
    );
    await expectWireError(
      "/api/setup/complete",
      { token: "tok-1", services: { clerk: { CLERK_SECRET_KEY: "sk_live_1" } } },
      { status: 500, code: "SETUP_REPO_ROOT_INVALID" },
    );
  });
});

describe("setup route coverage", () => {
  it("every route under app/api/setup is registered and exercised", () => {
    const setupRoot = path.join(webRoot, "app/api/setup");
    const fromSource: string[] = [];
    const walk = (dir: string): void => {
      for (const name of readdirSync(dir)) {
        const p = path.join(dir, name);
        if (statSync(p).isDirectory()) walk(p);
        else if (name === "route.ts" || name === "route.tsx") {
          fromSource.push(`/${path.relative(webRoot, path.dirname(p)).replace(/^app\//, "")}`);
        }
      }
    };
    walk(setupRoot);
    expect(fromSource.length).toBeGreaterThan(0);
    expect(fromSource.sort()).toEqual(Object.keys(SETUP_ROUTES).sort());
    expect([...exercised].sort()).toEqual(Object.keys(SETUP_ROUTES).sort());
  });
});
