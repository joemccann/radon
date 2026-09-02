/**
 * @vitest-environment jsdom
 *
 * Promoted UI preferences (theme, column visibility): profile row storage,
 * the debounced client sync module, and the ThemeContext cross-device fill.
 * The route half uses a real in-memory libsql client so the SQL executes for
 * real; the client half is asserted at the wire (full path, method, payload).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";
import { act, cleanup, render, waitFor } from "@testing-library/react";

// ── Auth mock ──────────────────────────────────────────
let currentUserId: string | null = "user_test_1";
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: currentUserId })),
}));

// ── DB mock — real in-memory libsql ─────────────────────
let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE user_profiles (
    user_id TEXT PRIMARY KEY, username TEXT, avatar_url TEXT,
    ui_preferences TEXT, updated_at TEXT NOT NULL)`);
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

beforeEach(async () => {
  vi.resetModules();
  currentUserId = "user_test_1";
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
  // jsdom has no matchMedia; ThemeContext's system-preference listener needs one.
  window.matchMedia =
    window.matchMedia ??
    ((query: string) =>
      ({
        matches: query.includes("dark"),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.localStorage.clear();
});

describe("profile route ui_preferences", () => {
  it("PUT persists ui_preferences and GET returns it parsed", async () => {
    const route = await import("../app/api/profile/route");
    const put = await route.PUT(
      new Request("http://x/api/profile", {
        method: "PUT",
        body: JSON.stringify({
          ui_preferences: { theme: "light", columns: { "orders-open": { qty: false } } },
        }),
      }),
    );
    expect(put.status).toBe(200);
    const get = await route.GET();
    const body = await jsonOf(get);
    expect(body.ui_preferences).toEqual({
      theme: "light",
      columns: { "orders-open": { qty: false } },
    });
  });

  it("PUT ui_preferences leaves username and avatar untouched (PATCH semantics)", async () => {
    const route = await import("../app/api/profile/route");
    await route.PUT(
      new Request("http://x/api/profile", {
        method: "PUT",
        body: JSON.stringify({ username: "operator" }),
      }),
    );
    await route.PUT(
      new Request("http://x/api/profile", {
        method: "PUT",
        body: JSON.stringify({ ui_preferences: { theme: "dark" } }),
      }),
    );
    const body = await jsonOf(await route.GET());
    expect(body.username).toBe("operator");
    expect(body.ui_preferences).toEqual({ theme: "dark" });
  });

  it("PUT ui_preferences merges per top-level key instead of replacing", async () => {
    const route = await import("../app/api/profile/route");
    await route.PUT(
      new Request("http://x/api/profile", {
        method: "PUT",
        body: JSON.stringify({
          ui_preferences: {
            theme: "dark",
            columns: { orders: { qty: false, status: true } },
          },
        }),
      }),
    );
    await route.PUT(
      new Request("http://x/api/profile", {
        method: "PUT",
        body: JSON.stringify({ ui_preferences: { theme: "light" } }),
      }),
    );
    const body = await jsonOf(await route.GET());
    expect(body.ui_preferences).toEqual({
      theme: "light",
      columns: { orders: { qty: false, status: true } },
    });
  });

  it("rejects unknown preference keys and bad theme values", async () => {
    const route = await import("../app/api/profile/route");
    const unknown = await route.PUT(
      new Request("http://x/api/profile", {
        method: "PUT",
        body: JSON.stringify({ ui_preferences: { evil: 1 } }),
      }),
    );
    expect(unknown.status).toBe(400);
    const badTheme = await route.PUT(
      new Request("http://x/api/profile", {
        method: "PUT",
        body: JSON.stringify({ ui_preferences: { theme: "hotdog" } }),
      }),
    );
    expect(badTheme.status).toBe(400);
  });
});

type RecordedCall = { url: string; method: string; body: unknown };

function stubFetch(profileBody: Record<string, unknown>): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({
        url: String(input),
        method,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return new Response(JSON.stringify(method === "GET" ? profileBody : {}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

describe("uiPreferences client module", () => {
  it("hydrates once from GET /api/profile, single-flight", async () => {
    const calls = stubFetch({ ui_preferences: { theme: "light" } });
    const mod = await import("../lib/uiPreferences");
    mod.__resetUiPreferencesForTests();
    const [a, b] = await Promise.all([
      mod.hydrateUiPreferences(),
      mod.hydrateUiPreferences(),
    ]);
    expect(a).toEqual({ theme: "light" });
    expect(b).toEqual({ theme: "light" });
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(1);
  });

  it("debounces saves into one PUT carrying the merged object", async () => {
    vi.useFakeTimers();
    const calls = stubFetch({ ui_preferences: null });
    const mod = await import("../lib/uiPreferences");
    mod.__resetUiPreferencesForTests();
    mod.saveUiTheme("light");
    mod.saveUiColumns("orders-open", { qty: false, status: true });
    mod.saveUiTheme("dark");
    await vi.advanceTimersByTimeAsync(1000);
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].url).toBe("/api/profile");
    expect(puts[0].body).toEqual({
      ui_preferences: {
        theme: "dark",
        columns: { "orders-open": { qty: false, status: true } },
      },
    });
  });

  it("mergeUiPreferences keeps local theme and adopts server columns", async () => {
    const mod = await import("../lib/uiPreferences");
    expect(
      mod.mergeUiPreferences(
        { theme: "dark" },
        { theme: "light", columns: { orders: { qty: false } } },
      ),
    ).toEqual({
      theme: "dark",
      columns: { orders: { qty: false } },
    });
  });

  it("theme toggle before hydrate settles still flushes server columns", async () => {
    vi.useFakeTimers();
    const calls = stubFetch({
      ui_preferences: { theme: "light", columns: { orders: { qty: false } } },
    });
    const mod = await import("../lib/uiPreferences");
    mod.__resetUiPreferencesForTests();
    const pending = mod.hydrateUiPreferences();
    mod.saveUiTheme("dark");
    await pending;
    await vi.advanceTimersByTimeAsync(1000);
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].body).toEqual({
      ui_preferences: { theme: "dark", columns: { orders: { qty: false } } },
    });
  });

  it("failed hydrate does not poison cache with an empty object", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 503 })),
    );
    const mod = await import("../lib/uiPreferences");
    mod.__resetUiPreferencesForTests();
    const prefs = await mod.hydrateUiPreferences();
    expect(prefs).toEqual({});
    mod.__seedUiPreferencesForTests({ theme: "dark" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ ui_preferences: { theme: "light" } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    mod.__resetUiPreferencesForTests();
    mod.__seedUiPreferencesForTests({ theme: "dark" });
    const retry = await mod.hydrateUiPreferences();
    expect(retry.theme).toBe("dark");
  });
});

describe("ThemeContext cross-device fill", () => {
  it("applies the stored server theme when this device has no explicit choice", async () => {
    stubFetch({ ui_preferences: { theme: "light" } });
    const prefs = await import("../lib/uiPreferences");
    prefs.__resetUiPreferencesForTests();
    document.documentElement.removeAttribute("data-theme");
    const { ThemeProvider, useTheme } = await import("../lib/ThemeContext");

    let observed: string | null = null;
    function Probe() {
      observed = useTheme().theme;
      return null;
    }
    await act(async () => {
      render(
        <ThemeProvider>
          <Probe />
        </ThemeProvider>,
      );
    });
    await waitFor(() => expect(observed).toBe("light"));
    expect(window.localStorage.getItem("theme")).toBe("light");
  });

  it("an explicit local choice beats the server value", async () => {
    stubFetch({ ui_preferences: { theme: "light" } });
    const prefs = await import("../lib/uiPreferences");
    prefs.__resetUiPreferencesForTests();
    window.localStorage.setItem("theme", "dark");
    document.documentElement.setAttribute("data-theme", "dark");
    const { ThemeProvider, useTheme } = await import("../lib/ThemeContext");

    let observed: string | null = null;
    function Probe() {
      observed = useTheme().theme;
      return null;
    }
    await act(async () => {
      render(
        <ThemeProvider>
          <Probe />
        </ThemeProvider>,
      );
    });
    // Give the hydrate promise a tick to settle; theme must stay dark.
    await act(async () => {
      await Promise.resolve();
    });
    expect(observed).toBe("dark");
  });
});
