import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

let tempRoot: string | null = null;

async function createTempRoot() {
  tempRoot = await mkdtemp(path.join(tmpdir(), "radon-newsfeed-auth-"));
  return tempRoot;
}

afterEach(async () => {
  if (tempRoot) {
    await rm(tempRoot, { recursive: true, force: true });
    tempRoot = null;
  }
});

type AnyFn = (...args: unknown[]) => unknown;
type LocatorOpts = { name?: RegExp };

interface FakeLocator {
  waitFor: AnyFn;
  click: AnyFn;
  fill: AnyFn;
  press: AnyFn;
  first: () => FakeLocator;
  getByRole: (role: string, opts?: LocatorOpts) => FakeLocator;
}

function createFakeLocator(record: { calls: Array<{ method: string; args: unknown[] }> }): FakeLocator {
  const push = (method: string, args: unknown[]) =>
    record.calls.push({ method, args });
  const locator: FakeLocator = {
    waitFor: async (...args: unknown[]) => push("waitFor", args),
    click: async (...args: unknown[]) => push("click", args),
    fill: async (...args: unknown[]) => push("fill", args),
    press: async (...args: unknown[]) => push("press", args),
    first: () => locator,
    getByRole: (_role: string, _opts?: LocatorOpts) => locator,
  };
  return locator;
}

interface FakePageOpts {
  initialUrl?: string;
  postLoginUrl?: string;
  // Result returned by `page.evaluate(...)` — the auth flow uses this to
  // detect paywall-stub bodies on a session that has degraded to free
  // tier. Default true = "looks like premium content," matching the
  // happy path. Set false to simulate the silent-paywall regression.
  premiumContentPresent?: boolean;
}

function createFakePage(opts: FakePageOpts = {}) {
  const calls: Array<{ method: string; args: unknown[] }> = [];
  const locatorRecord = { calls };
  let currentUrl = opts.initialUrl ?? "https://themarketear.com/the-newsletter";
  const premium = opts.premiumContentPresent ?? true;

  const page = {
    url: () => currentUrl,
    goto: async (url: string) => {
      calls.push({ method: "goto", args: [url] });
      currentUrl = url;
      return null;
    },
    waitForLoadState: async (...args: unknown[]) => {
      calls.push({ method: "waitForLoadState", args });
    },
    waitForURL: async (pattern: RegExp) => {
      calls.push({ method: "waitForURL", args: [pattern] });
      if (opts.postLoginUrl) currentUrl = opts.postLoginUrl;
    },
    waitForResponse: async (..._args: unknown[]) => null,
    locator: (_sel: string) => createFakeLocator(locatorRecord),
    screenshot: async (...args: unknown[]) => calls.push({ method: "screenshot", args }),
    evaluate: async (..._args: unknown[]) => premium,
    on: () => {},
  };
  return { page, calls };
}

describe("readCredentialsFromEnv", () => {
  it("returns trimmed email and password from env", async () => {
    const { readCredentialsFromEnv } = await import("../../scripts/newsfeed/auth.js");
    const creds = readCredentialsFromEnv({
      THEMARKETEAR_EMAIL: " joe@example.com ",
      THEMARKETEAR_PASSWORD: "  hunter2  ",
    });
    expect(creds).toEqual({ email: "joe@example.com", password: "hunter2" });
  });

  it("throws when credentials missing", async () => {
    const { readCredentialsFromEnv, NewsfeedAuthError } = await import(
      "../../scripts/newsfeed/auth.js"
    );
    expect(() => readCredentialsFromEnv({})).toThrow(NewsfeedAuthError);
    expect(() => readCredentialsFromEnv({ THEMARKETEAR_EMAIL: "joe@x" })).toThrow(/password/i);
  });
});

describe("ensureAuthenticated — storage state fresh path", () => {
  it("skips login flow but PERSISTS storage state when initial /newsfeed visit lands on the authenticated newsfeed", async () => {
    const { ensureAuthenticated } = await import("../../scripts/newsfeed/auth.js");
    const { page, calls } = createFakePage({
      initialUrl: "https://themarketear.com/the-newsletter",
      postLoginUrl: "https://themarketear.com/newsfeed",
    });

    page.goto = async (url: string) => {
      calls.push({ method: "goto", args: [url] });
      // simulate fresh storage state — first goto succeeds
      (page as unknown as { url: () => string }).url = () => "https://themarketear.com/newsfeed";
      return null;
    };

    let persistCallCount = 0;
    const result = await ensureAuthenticated({
      context: {},
      page: page as never,
      credentials: { email: "joe@x", password: "pw" },
      persistStorageState: async () => {
        persistCallCount += 1;
      },
    });

    expect(result).toEqual({ authenticated: true, reusedSession: true });
    expect(calls.find((c) => c.method === "fill")).toBeUndefined();
    // Warm-reuse path MUST refresh disk-side cookies so a process restart
    // doesn't fall back onto stale storage state.
    expect(persistCallCount).toBe(1);
  });

  it("persists storage state on every warm-reuse invocation (multi-cycle)", async () => {
    const { ensureAuthenticated } = await import("../../scripts/newsfeed/auth.js");
    const { page, calls } = createFakePage({
      initialUrl: "https://themarketear.com/the-newsletter",
      postLoginUrl: "https://themarketear.com/newsfeed",
    });

    page.goto = async (url: string) => {
      calls.push({ method: "goto", args: [url] });
      (page as unknown as { url: () => string }).url = () => "https://themarketear.com/newsfeed";
      return null;
    };

    let persistCallCount = 0;
    const persistFn = async () => {
      persistCallCount += 1;
    };

    for (let i = 0; i < 3; i += 1) {
      const result = await ensureAuthenticated({
        context: {},
        page: page as never,
        credentials: { email: "joe@x", password: "pw" },
        persistStorageState: persistFn,
      });
      expect(result.reusedSession).toBe(true);
    }

    // Each warm reuse must persist; the stale-storage bug surfaced precisely
    // because the happy path skipped persistence.
    expect(persistCallCount).toBe(3);
  });
});

// Regression 2026-05-17: themarketear.com kept serving /newsfeed even when
// the session had degraded to free tier — same URL, same article cards,
// but every body replaced with "part of our Premium coverage" stub text.
// The URL-only auth check accepted this and the extractor silently saved
// paywall stubs into posts.json. Fix: also sniff the DOM for premium
// content before trusting the reused session.
describe("ensureAuthenticated — silent paywall detection", () => {
  it("forces login flow when /newsfeed returns paywall-stub bodies", async () => {
    const { ensureAuthenticated } = await import("../../scripts/newsfeed/auth.js");

    // /newsfeed loads (URL check passes) but pageHasPremiumContent returns
    // false → tryReachNewsfeed must reject the reused session and run the
    // full login flow. `waitForURL` brings the URL back to /newsfeed
    // after the login submit, so the post-flow URL check passes.
    const { page, calls } = createFakePage({
      initialUrl: "https://themarketear.com/the-newsletter",
      postLoginUrl: "https://themarketear.com/newsfeed",
      premiumContentPresent: false,
    });

    let persistCallCount = 0;
    const result = await ensureAuthenticated({
      context: {},
      page: page as never,
      credentials: { email: "joe@x", password: "pw" },
      persistStorageState: async () => {
        persistCallCount += 1;
      },
    });

    // Login flow must have run (proves we didn't trust the stub-only session).
    expect(result).toEqual({ authenticated: true, reusedSession: false });
    expect(calls.some((c) => c.method === "fill")).toBe(true);
    // Fresh storage persisted after the login flow.
    expect(persistCallCount).toBe(1);
  });
});

describe("ensureAuthenticated — storage state stale path", () => {
  it("runs full login flow and persists storage state when initial visit fails", async () => {
    const { ensureAuthenticated } = await import("../../scripts/newsfeed/auth.js");

    const calls: Array<{ method: string; args: unknown[] }> = [];
    const locatorRecord = { calls };
    let urlValue = "https://themarketear.com/the-newsletter";

    const page = {
      url: () => urlValue,
      goto: async (url: string) => {
        calls.push({ method: "goto", args: [url] });
        // First goto stays on /the-newsletter (anon redirect),
        // forcing the login flow.
        if (url.includes("/newsfeed")) {
          urlValue = "https://themarketear.com/the-newsletter";
        } else {
          urlValue = url;
        }
      },
      waitForLoadState: async (...args: unknown[]) => {
        calls.push({ method: "waitForLoadState", args });
      },
      waitForURL: async (pattern: RegExp) => {
        calls.push({ method: "waitForURL", args: [pattern] });
        urlValue = "https://themarketear.com/newsfeed";
      },
      waitForResponse: async (..._args: unknown[]) => null,
      locator: (_sel: string) => createFakeLocator(locatorRecord),
      screenshot: async (...args: unknown[]) => calls.push({ method: "screenshot", args }),
      evaluate: async () => null,
      on: () => {},
    };

    let persistCalled = false;
    const result = await ensureAuthenticated({
      context: {},
      page: page as never,
      credentials: { email: "joe@x", password: "pw" },
      persistStorageState: async () => {
        persistCalled = true;
      },
    });

    expect(result).toEqual({ authenticated: true, reusedSession: false });
    const fills = calls.filter((c) => c.method === "fill").map((c) => c.args[0]);
    expect(fills).toContain("joe@x");
    expect(fills).toContain("pw");
    expect(persistCalled).toBe(true);
  });
});

describe("ensureAuthenticated — failure capture", () => {
  it("captures debug screenshot and throws NewsfeedAuthError when login flow fails", async () => {
    const { ensureAuthenticated, NewsfeedAuthError } = await import(
      "../../scripts/newsfeed/auth.js"
    );

    const debugDir = await createTempRoot();

    const failingLocator = {
      waitFor: async () => {
        throw new Error("modal never appeared");
      },
      click: async () => {},
      fill: async () => {},
      press: async () => {},
      first() {
        return this;
      },
      getByRole(): typeof this {
        return this;
      },
    };

    const page = {
      url: () => "https://themarketear.com/",
      goto: async () => {},
      waitForLoadState: async () => {},
      waitForURL: async () => {},
      waitForResponse: async () => null,
      locator: () => failingLocator,
      screenshot: async ({ path: dest }: { path: string }) => {
        await writeFile(dest, Buffer.from("fake-png"));
      },
      evaluate: async () => null,
      on: () => {},
    };

    await expect(
      ensureAuthenticated({
        context: {},
        page: page as never,
        credentials: { email: "joe@x", password: "pw" },
        persistStorageState: async () => {},
        debugDir,
      }),
    ).rejects.toThrow(NewsfeedAuthError);

    const files = await readdir(debugDir);
    expect(files.some((name) => name.startsWith("newsfeed-debug-") && name.endsWith(".png"))).toBe(true);
  });
});


describe("ensureAuthenticated — proactive Firebase renewal", () => {
  it("renews an otherwise warm premium cookie without using the password flow", async () => {
    const { ensureAuthenticated } = await import("../../scripts/newsfeed/auth.js");
    const { page, calls } = createFakePage();
    let renewed = false;
    const result = await ensureAuthenticated({ context: {}, page, env: {},
      renewSession: async () => { renewed = true; return true; },
      persistStorageState: async () => { expect(renewed).toBe(true); },
    });
    expect(result).toEqual({ authenticated: true, reusedSession: true, renewedSession: true });
    expect(calls.some(c => c.method === "fill")).toBe(false);
  });
  it("requires premium content after renewal and never accepts paywall stubs", async () => {
    const { ensureAuthenticated } = await import("../../scripts/newsfeed/auth.js");
    const { page, calls } = createFakePage({ premiumContentPresent: false });
    await expect(ensureAuthenticated({ context: {}, page, env: {}, renewSession: async () => true })).rejects.toThrow(/premium/);
    expect(calls.some(c => c.method === "fill")).toBe(false);
  });
  it("retains a currently valid cookie if the renewal provider is temporarily unavailable", async () => {
    const { ensureAuthenticated } = await import("../../scripts/newsfeed/auth.js");
    const { page } = createFakePage();
    await expect(ensureAuthenticated({ context: {}, page, env: {}, renewSession: async () => { throw new Error("unavailable"); } })).resolves.toEqual({ authenticated: true, reusedSession: true });
  });
});
