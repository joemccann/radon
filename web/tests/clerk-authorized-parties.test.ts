/**
 * CLERK_AUTHORIZED_PARTIES binds a Clerk session token to the app it was
 * minted for: web/middleware.ts passes the parsed list to clerkMiddleware as
 * `authorizedParties`, so a token whose azp names another app is refused.
 * Unset keeps Clerk's default (no azp check). Mirrors the FastAPI and hosted
 * MCP verifiers (scripts/tests/test_clerk_authorized_parties.py).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { NextResponse } from "next/server";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.doUnmock("@clerk/nextjs/server");
});

async function middlewareOptions(): Promise<Record<string, unknown>> {
  const calls: unknown[][] = [];
  vi.doMock("@clerk/nextjs/server", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@clerk/nextjs/server")>();
    return {
      ...actual,
      clerkMiddleware: (...args: unknown[]) => {
        calls.push(args);
        return () => NextResponse.next();
      },
    };
  });
  await import("../middleware");
  expect(calls).toHaveLength(1);
  return calls[0][1] as Record<string, unknown>;
}

describe("Clerk authorized parties", () => {
  it("passes the configured origins to clerkMiddleware", async () => {
    vi.stubEnv("CLERK_AUTHORIZED_PARTIES", " https://app.example.test , http://localhost:3000,");
    const options = await middlewareOptions();
    expect(options.authorizedParties).toEqual(["https://app.example.test", "http://localhost:3000"]);
    expect(options).toMatchObject({ frontendApiProxy: { enabled: false } });
  });

  it("leaves the azp check off when unset", async () => {
    vi.stubEnv("CLERK_AUTHORIZED_PARTIES", "");
    const options = await middlewareOptions();
    expect(options.authorizedParties).toBeUndefined();
  });
});

describe("installed clerkMiddleware honors authorizedParties", () => {
  it("refuses a bearer token whose azp is not listed and admits a listed one", async () => {
    const { generateKeyPairSync, createSign } = await import("node:crypto");
    const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
    const jwtKey = publicKey.export({ type: "spki", format: "pem" }).toString();
    const b64 = (v: object | Buffer) =>
      (Buffer.isBuffer(v) ? v : Buffer.from(JSON.stringify(v))).toString("base64url");
    const token = (azp: string) => {
      const now = Math.floor(Date.now() / 1000);
      const head = b64({ alg: "RS256", typ: "JWT", kid: "ins_test" });
      const body = b64({ sub: "user_operator", sid: "sess_test", azp, iat: now - 5, nbf: now - 5, exp: now + 300, iss: "https://clerk.radon.example" });
      const sig = createSign("RSA-SHA256").update(`${head}.${body}`).sign(privateKey);
      return `${head}.${body}.${b64(sig)}`;
    };
    const fetchMock = vi.fn(async () => new Response("{}", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", `pk_live_${Buffer.from("clerk.radon.example$").toString("base64")}`);
    vi.stubEnv("CLERK_SECRET_KEY", "sk_live_test");
    vi.resetModules();
    const { clerkMiddleware } = await import("@clerk/nextjs/server");
    const { NextRequest } = await import("next/server");
    const seen: (string | null)[] = [];
    const mw = clerkMiddleware(
      async (auth) => {
        seen.push((await auth()).userId);
        return NextResponse.next();
      },
      {
        jwtKey,
        authorizedParties: ["https://app.example.test"],
      },
    );
    const event = { waitUntil: () => {} } as never;
    const call = (azp: string) =>
      mw(new NextRequest("https://app.example.test/api/portfolio", { headers: { authorization: `Bearer ${token(azp)}` } }), event);
    await call("https://other.example.test");
    await call("https://app.example.test");
    expect(seen).toEqual([null, "user_operator"]);
    vi.unstubAllGlobals();
  });
});
