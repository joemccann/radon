/**
 * T-449: Pushover operator alerting asserted at the wire, not as source text.
 *
 * Replaces the route-text greps in demo-provisioning-resilience.test.ts
 * (`toContain("notifyDemoProvisioningFailure")`, the email/userId regexes)
 * with behavioural pins: fetch is stubbed and the contract is read off the
 * actual request — (a) no wire call without credentials, (b) a Pushover 500
 * or network failure never throws into the caller, (c) the payload carries
 * only the fixed field set, never an email or user id.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sendPushover, notifyDemoProvisioningFailure } from "@/lib/notify/pushover";
import { POST as clerkWebhookPost } from "@/app/api/webhooks/clerk/route";

vi.mock("@/lib/db", () => ({ getDemoDb: () => ({}) }));
vi.mock("@/lib/demo/demoUsers", () => ({
  claimDemoWebhookEvent: vi.fn().mockResolvedValue(true),
  releaseDemoWebhookEvent: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@/lib/demo/provisionTrial", () => ({
  provisionDemoTrial: vi.fn().mockRejectedValue(new Error("no such table: demo_users")),
  parseAllowedUserIds: () => new Set<string>(),
}));

const PUSHOVER_URL = "https://api.pushover.net/1/messages.json";

let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch(response: { ok: boolean; status: number }) {
  fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  vi.stubEnv("PUSHOVER_TOKEN", "apptoken_stub");
  vi.stubEnv("PUSHOVER_USER", "userkey_stub");
  stubFetch({ ok: true, status: 200 });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("sendPushover credential gate", () => {
  it("makes no wire call at all when credentials are absent", async () => {
    vi.stubEnv("PUSHOVER_TOKEN", "");
    vi.stubEnv("PUSHOVER_USER", "");
    await expect(sendPushover({ title: "t", message: "m" })).resolves.toBe(false);
    await expect(notifyDemoProvisioningFailure("reason")).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("makes no wire call when only one credential is present", async () => {
    vi.stubEnv("PUSHOVER_USER", "");
    await expect(sendPushover({ title: "t", message: "m" })).resolves.toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("sendPushover never throws into the caller", () => {
  it("resolves false when Pushover returns 500", async () => {
    stubFetch({ ok: false, status: 500 });
    await expect(sendPushover({ title: "t", message: "m" })).resolves.toBe(false);
    await expect(notifyDemoProvisioningFailure("reason")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalled();
  });

  it("resolves false when the network request itself rejects", async () => {
    fetchMock = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(sendPushover({ title: "t", message: "m" })).resolves.toBe(false);
    await expect(notifyDemoProvisioningFailure("reason")).resolves.toBeUndefined();
  });
});

describe("notifyDemoProvisioningFailure payload is PII-free", () => {
  it("sends exactly the credential and content fields — never an email or user id", async () => {
    await notifyDemoProvisioningFailure("no such table: demo_webhook_events");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(PUSHOVER_URL);
    expect(init.method).toBe("POST");

    const body = init.body as URLSearchParams;
    expect([...body.keys()].sort()).toEqual([
      "message",
      "title",
      "token",
      "url",
      "url_title",
      "user",
    ]);
    expect(body.get("title")).toBe("radon demo provisioning failed");
    expect(body.get("message")).toBe(
      "A demo.radon.run signup was not granted a trial: no such table: demo_webhook_events",
    );
    expect(body.get("url")).toBe("https://demo.radon.run/sign-up");
    expect(body.get("url_title")).toBe("demo sign-up");
    expect(body.toString()).not.toMatch(/email|userId|user_id/i);
  });
});

describe("clerk webhook route pages the operator on a provisioning failure, without PII", () => {
  // Dummy HMAC key built from fixed bytes at runtime — no secret-looking
  // literal in source (same construction as demo-svix-verify.test.ts).
  const KEY_BYTES = new Uint8Array(Array.from({ length: 24 }, (_, i) => (i * 7 + 13) % 256));
  const SECRET = `whsec_${btoa(String.fromCharCode(...KEY_BYTES))}`;

  async function sign(body: string, id: string, ts: string): Promise<string> {
    const key = await crypto.subtle.importKey(
      "raw",
      KEY_BYTES,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const mac = await crypto.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(`${id}.${ts}.${body}`),
    );
    return `v1,${btoa(String.fromCharCode(...new Uint8Array(mac)))}`;
  }

  it("fires exactly one Pushover alert whose payload omits the signup's email and user id", async () => {
    vi.stubEnv("CLERK_WEBHOOK_SECRET", SECRET);
    const body = JSON.stringify({
      type: "user.created",
      data: {
        id: "user_2PIIexample",
        email_addresses: [{ email_address: "signup-victim@example.com" }],
        unsafe_metadata: { demo: true },
      },
    });
    const id = "msg_t449";
    const ts = String(Math.floor(Date.now() / 1000));
    const res = await clerkWebhookPost(
      new Request("https://demo.radon.run/api/webhooks/clerk", {
        method: "POST",
        headers: {
          "svix-id": id,
          "svix-timestamp": ts,
          "svix-signature": await sign(body, id, ts),
        },
        body,
      }),
    );
    expect(res.status).toBe(500);

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(PUSHOVER_URL);
    const wire = (init.body as URLSearchParams).toString();
    expect(wire).toContain("radon+demo+provisioning+failed");
    expect(wire).not.toContain("signup-victim");
    expect(wire).not.toContain("example.com");
    expect(wire).not.toContain("user_2PIIexample");
  });
});
