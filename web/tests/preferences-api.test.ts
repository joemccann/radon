import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Preferences proxy route (`web/app/api/preferences/route.ts`) plus the browser
 * helpers in `web/lib/preferences.ts`.
 *
 * The route is a thin authenticated proxy in front of FastAPI `/preferences`.
 * GET requires a signed-in user; PUT and DELETE require the operator allowlist
 * (default-deny). Upstream status codes are preserved so a 422 validation
 * refusal never becomes a 500.
 */

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

class MockRadonApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`Radon API ${status}: ${detail}`);
    this.name = "RadonApiError";
    this.status = status;
    this.detail = detail;
  }
}

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: MockRadonApiError,
}));

let currentUserId: string | null = "user_signed_in";
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: currentUserId })),
}));

const mockRequireDemoAdmin = vi.fn();
vi.mock("@/lib/demo/adminAuth", () => ({
  requireDemoAdmin: mockRequireDemoAdmin,
}));

// ---------------------------------------------------------------------------
// Fixtures / helpers
// ---------------------------------------------------------------------------

const ENTRY = {
  key: "RADON_MAX_ORDER_QTY",
  label: "Max contracts per order",
  group: "Order Limits",
  value_type: "int",
  value: 400,
  default: 500,
  hard_min: 1,
  hard_max: 5000,
  unit: "contracts",
  description: "Hard ceiling on contracts per options or combo order.",
  applies_immediately: true,
  source: "db",
  db_rejected: false,
  updated_at: "2026-08-11T17:02:11Z",
  updated_by: "user_x",
};

const STORE = { available: true, error: null, checked_at: "2026-08-11T17:02:11Z" };

const PAYLOAD = {
  preferences: [ENTRY],
  groups: ["Order Limits", "Scanning", "Feature Flags", "IB Recovery", "Health Monitoring"],
  store: STORE,
  generated_at: "2026-08-11T17:02:11Z",
};

function req(url: string, init?: RequestInit): Request {
  return new Request(url, init);
}

function putReq(body: string): Request {
  return req("http://localhost/api/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body,
  });
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

async function loadRoute() {
  return import("../app/api/preferences/route");
}

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
  mockRequireDemoAdmin.mockReset();
  mockRequireDemoAdmin.mockResolvedValue("user_x");
  currentUserId = "user_signed_in";
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// GET
// ---------------------------------------------------------------------------

describe("GET /api/preferences", () => {
  it("returns 401 UNAUTHORIZED when no user is signed in", async () => {
    currentUserId = null;
    const { GET } = await loadRoute();
    const res = await GET();
    expect(res.status).toBe(401);
    const body = await jsonOf(res);
    expect(body.code).toBe("UNAUTHORIZED");
    expect(body.error).toBe("Sign in required");
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("proxies to /preferences and returns the upstream payload unchanged", async () => {
    mockRadonFetch.mockResolvedValueOnce(PAYLOAD);
    const { GET } = await loadRoute();
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual(PAYLOAD);
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/preferences",
      expect.objectContaining({ method: "GET", timeout: 15_000 }),
    );
  });

  it("sets no-store cache headers and a request id", async () => {
    mockRadonFetch.mockResolvedValueOnce(PAYLOAD);
    const { GET } = await loadRoute();
    const res = await GET();
    expect(res.headers.get("Cache-Control")).toContain("no-store");
    expect(res.headers.get("X-Request-Id")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// PUT
// ---------------------------------------------------------------------------

describe("PUT /api/preferences", () => {
  it("returns 403 FORBIDDEN and never calls FastAPI without an operator", async () => {
    mockRequireDemoAdmin.mockResolvedValue(null);
    const { PUT } = await loadRoute();
    const res = await PUT(putReq(JSON.stringify({ key: "RADON_MAX_ORDER_QTY", value: 400 })));
    expect(res.status).toBe(403);
    const body = await jsonOf(res);
    expect(body.code).toBe("FORBIDDEN");
    expect(body.error).toBe("Operator authorization required");
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("forwards the value and the operator id to FastAPI", async () => {
    mockRadonFetch.mockResolvedValueOnce({ preference: ENTRY, store: STORE });
    const { PUT } = await loadRoute();
    const res = await PUT(putReq(JSON.stringify({ key: "RADON_MAX_ORDER_QTY", value: 400 })));
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({ preference: ENTRY, store: STORE });
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/preferences/RADON_MAX_ORDER_QTY",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ value: 400, updated_by: "user_x" }),
      }),
    );
  });

  it("returns 400 VALIDATION_ERROR when key is missing", async () => {
    const { PUT } = await loadRoute();
    const res = await PUT(putReq(JSON.stringify({ value: 400 })));
    expect(res.status).toBe(400);
    const body = await jsonOf(res);
    expect(body.code).toBe("VALIDATION_ERROR");
    expect(body.error).toBe("key is required");
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("returns 400 VALIDATION_ERROR when value is missing", async () => {
    const { PUT } = await loadRoute();
    const res = await PUT(putReq(JSON.stringify({ key: "RADON_MAX_ORDER_QTY" })));
    expect(res.status).toBe(400);
    const body = await jsonOf(res);
    expect(body.code).toBe("VALIDATION_ERROR");
    expect(body.error).toBe("value is required");
  });

  it("returns 400 BAD_REQUEST on an unparseable body", async () => {
    const { PUT } = await loadRoute();
    const res = await PUT(putReq("{not json"));
    expect(res.status).toBe(400);
    const body = await jsonOf(res);
    expect(body.code).toBe("BAD_REQUEST");
    expect(body.error).toBe("Invalid JSON body");
  });

  it("URL-encodes the key into the upstream path", async () => {
    mockRadonFetch.mockResolvedValueOnce({ preference: ENTRY, store: STORE });
    const { PUT } = await loadRoute();
    await PUT(putReq(JSON.stringify({ key: "WEIRD KEY/../x", value: 1 })));
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/preferences/WEIRD%20KEY%2F..%2Fx",
      expect.anything(),
    );
  });

  it("preserves an upstream 422 with the validation message, not a 500", async () => {
    const message = "RADON_MAX_ORDER_QTY must be between 1 and 5000 (got 9000)";
    mockRadonFetch.mockRejectedValueOnce(new MockRadonApiError(422, message));
    const { PUT } = await loadRoute();
    const res = await PUT(putReq(JSON.stringify({ key: "RADON_MAX_ORDER_QTY", value: 9000 })));
    expect(res.status).toBe(422);
    const body = await jsonOf(res);
    expect(body.error).toBe(message);
    expect(body.code).toBe("UPSTREAM_ERROR");
  });

  it("preserves an upstream 503 as DB_UNAVAILABLE", async () => {
    mockRadonFetch.mockRejectedValueOnce(
      new MockRadonApiError(503, "preferences store temporarily unavailable"),
    );
    const { PUT } = await loadRoute();
    const res = await PUT(putReq(JSON.stringify({ key: "RADON_MAX_ORDER_QTY", value: 400 })));
    expect(res.status).toBe(503);
    expect((await jsonOf(res)).code).toBe("DB_UNAVAILABLE");
  });

  it("maps a non-RadonApiError throw to 502 UPSTREAM_ERROR", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("socket hang up"));
    const { PUT } = await loadRoute();
    const res = await PUT(putReq(JSON.stringify({ key: "RADON_MAX_ORDER_QTY", value: 400 })));
    expect(res.status).toBe(502);
    const body = await jsonOf(res);
    expect(body.code).toBe("UPSTREAM_ERROR");
    expect(body.error).toContain("socket hang up");
  });
});

// ---------------------------------------------------------------------------
// DELETE
// ---------------------------------------------------------------------------

describe("DELETE /api/preferences", () => {
  it("returns 403 without an operator", async () => {
    mockRequireDemoAdmin.mockResolvedValue(null);
    const { DELETE } = await loadRoute();
    const res = await DELETE(
      req("http://localhost/api/preferences?key=RADON_MAX_ORDER_QTY", { method: "DELETE" }),
    );
    expect(res.status).toBe(403);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("clears the stored override through FastAPI with the encoded key", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      preference: { ...ENTRY, source: "default", value: 500 },
      store: STORE,
    });
    const { DELETE } = await loadRoute();
    const res = await DELETE(
      req("http://localhost/api/preferences?key=RADON_MAX_ORDER_QTY", { method: "DELETE" }),
    );
    expect(res.status).toBe(200);
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/preferences/RADON_MAX_ORDER_QTY?updated_by=user_x",
      expect.objectContaining({ method: "DELETE", timeout: 15_000 }),
    );
    const body = (await jsonOf(res)) as { preference: Record<string, unknown> };
    expect(body.preference.source).toBe("default");
  });

  it("returns 400 VALIDATION_ERROR when the key query param is missing", async () => {
    const { DELETE } = await loadRoute();
    const res = await DELETE(req("http://localhost/api/preferences", { method: "DELETE" }));
    expect(res.status).toBe(400);
    const body = await jsonOf(res);
    expect(body.code).toBe("VALIDATION_ERROR");
    expect(body.error).toBe("key is required");
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// web/lib/preferences.ts helpers
// ---------------------------------------------------------------------------

describe("preferences client helpers", () => {
  const boolEntry = {
    ...ENTRY,
    key: "RADON_KB_EMBED_DISABLED",
    group: "Feature Flags",
    value_type: "bool" as const,
    value: true,
    default: false,
    hard_min: false,
    hard_max: true,
    unit: "",
    applies_immediately: false,
  };

  it("groupPreferences respects the declared group order and drops empty groups", async () => {
    const { groupPreferences } = await import("../lib/preferences");
    const grouped = groupPreferences(
      [
        { ...ENTRY, key: "A" },
        { ...boolEntry, key: "B" },
        { ...ENTRY, key: "C", group: "Zebra" },
      ] as never,
      ["Order Limits", "Scanning", "Feature Flags"],
    );
    expect(grouped.map((g) => g.group)).toEqual(["Order Limits", "Feature Flags", "Zebra"]);
    expect(grouped[0].entries.map((e) => e.key)).toEqual(["A"]);
  });

  it("formatPreferenceValue renders booleans and appends the unit", async () => {
    const { formatPreferenceValue } = await import("../lib/preferences");
    expect(formatPreferenceValue(boolEntry as never, true)).toBe("On");
    expect(formatPreferenceValue(boolEntry as never, false)).toBe("Off");
    expect(formatPreferenceValue(ENTRY as never, 5000)).toBe("5,000 contracts");
    expect(
      formatPreferenceValue({ ...ENTRY, value_type: "float", unit: "USD" } as never, 250000.5),
    ).toBe("250,000.5 USD");
  });

  it("parsePreferenceInput rejects empty, non-numeric, non-integral and out-of-band values", async () => {
    const { parsePreferenceInput } = await import("../lib/preferences");
    expect(parsePreferenceInput(ENTRY as never, "   ")).toEqual({ error: "value is required" });
    expect(parsePreferenceInput(ENTRY as never, "abc")).toEqual({ error: "value must be a number" });
    expect(parsePreferenceInput(ENTRY as never, "400.5")).toEqual({
      error: "value must be a whole number",
    });
    expect(parsePreferenceInput(ENTRY as never, "9000")).toEqual({
      error: "value must be between 1 and 5000",
    });
    expect(parsePreferenceInput(ENTRY as never, " 400 ")).toEqual({ value: 400 });
  });

  it("savePreference throws PreferencesRequestError carrying the server code and message", async () => {
    const { savePreference, PreferencesRequestError } = await import("../lib/preferences");
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: "RADON_MAX_ORDER_QTY must be between 1 and 5000 (got 9000)", code: "UPSTREAM_ERROR" }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchSpy);
    await expect(savePreference("RADON_MAX_ORDER_QTY", 9000)).rejects.toBeInstanceOf(
      PreferencesRequestError,
    );
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/preferences",
      expect.objectContaining({ method: "PUT" }),
    );
    vi.unstubAllGlobals();
  });

  it("fetchPreferences and resetPreference hit the proxy endpoints", async () => {
    const { fetchPreferences, resetPreference } = await import("../lib/preferences");
    const fetchSpy = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(PAYLOAD), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    await expect(fetchPreferences()).resolves.toEqual(PAYLOAD);
    await resetPreference("RADON_MAX_ORDER_QTY");
    expect(fetchSpy).toHaveBeenLastCalledWith(
      "/api/preferences?key=RADON_MAX_ORDER_QTY",
      expect.objectContaining({ method: "DELETE" }),
    );
    vi.unstubAllGlobals();
  });
});
