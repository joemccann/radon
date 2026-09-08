/**
 * RC-B9/B18: every outbound surface that carries upstream error text must run
 * it through scrubSecrets. The scanner routes returned raw error.message and
 * the demo-provisioning Pushover alert interpolated the raw failure reason,
 * both of which can carry a LibsqlError's connection URL + auth token.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/radonApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/radonApi")>();
  return { ...actual, radonFetch: mocks.radonFetch };
});
vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: vi.fn(async () => ({ ok: true, principal: { userId: "u", kind: "operator" } })),
}));

// Low-entropy on purpose: the scrubber matches the whole libsql:// URL, and a
// realistic-looking token here would trip the gitleaks CI gate.
const LEAKY = "connect failed: libsql://radon-secret.turso.io?authToken=fake-token-fake-token-fake";

describe("RC-B9: scanner routes scrub upstream error text", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.radonFetch.mockReset();
    mocks.radonFetch.mockRejectedValue(new Error(LEAKY));
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("POST /api/scanner redacts the DB URL when the cache fallback also fails", async () => {
    const { POST } = await import("@/app/api/scanner/route");
    const res = await POST();
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error?: string };
    expect(body.error ?? "").not.toContain("libsql://");
    expect(body.error ?? "").not.toContain("turso.io");
    expect(body.error).toContain("[redacted-db-url]");
  });

  it("POST /api/scanner/theta/scan redacts the DB URL in the error payload", async () => {
    const { POST } = await import("@/app/api/scanner/theta/scan/route");
    const res = await POST(
      new Request("http://localhost/api/scanner/theta/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: "SPY" }),
      }),
    );
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error?: string };
    expect(body.error ?? "").not.toContain("libsql://");
    expect(body.error ?? "").not.toContain("turso.io");
    expect(body.error).toContain("[redacted-db-url]");
  });
});

describe("RC-B18: demo provisioning Pushover alert scrubs the reason", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.stubEnv("PUSHOVER_TOKEN", "apptoken");
    vi.stubEnv("PUSHOVER_USER", "userkey");
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("redacts a libsql URL and bounds the message", async () => {
    const { notifyDemoProvisioningFailure } = await import("@/lib/notify/pushover");
    await notifyDemoProvisioningFailure(`${LEAKY} ${"x".repeat(2000)}`);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body = fetchMock.mock.calls[0][1]?.body as URLSearchParams;
    const message = body.get("message") ?? "";
    expect(message).not.toContain("libsql://");
    expect(message).not.toContain("turso.io");
    expect(message).toContain("[redacted-db-url]");
    expect(message.length).toBeLessThan(650);
  });
});
