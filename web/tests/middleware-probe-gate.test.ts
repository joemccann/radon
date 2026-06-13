/**
 * DUR-16: the bearer-gated freshness-probe perimeter.
 *
 * `/api/probe/freshness` is a DELIBERATE perimeter change: the Tier-3
 * off-box prober (GitHub Actions, no Clerk session) authenticates with
 * `Authorization: Bearer ${RADON_PROBE_FRESHNESS_TOKEN}` instead. The gate
 * lives in the middleware (the perimeter — feedback_middleware_is_the_perimeter)
 * and the token compare MUST be timing-safe and Edge-runtime-safe (Web
 * Crypto only — no node:* in the middleware graph).
 *
 * Pins:
 *   1. Missing/malformed/wrong bearer -> 401 JSON (no detail about why).
 *   2. Correct bearer -> pass-through (NextResponse.next()).
 *   3. Unset server token -> closed perimeter (401 for everything).
 *   4. Non-probe paths are untouched by the gate (null = fall through to Clerk).
 *   5. The compare helper is timing-safe-by-construction (digest-then-compare).
 */
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import {
  handleProbeBearerGate,
  isProbeBearerRoute,
  PROBE_BEARER_API_ROUTES,
} from "../middleware";
import {
  bearerTokenFrom,
  isAuthorizedProbeRequest,
  timingSafeStringEqual,
} from "../lib/probeAuth";

const TOKEN = "rpf_test_token_42";

function reqFor(pathname: string, authorization?: string): NextRequest {
  return new NextRequest(`https://app.radon.run${pathname}`, {
    headers: authorization ? { authorization } : undefined,
  });
}

describe("timingSafeStringEqual — Web Crypto digest-then-compare", () => {
  it("equal strings compare true", async () => {
    expect(await timingSafeStringEqual("abc123", "abc123")).toBe(true);
    expect(await timingSafeStringEqual("", "")).toBe(true);
  });

  it("different strings compare false", async () => {
    expect(await timingSafeStringEqual("abc123", "abc124")).toBe(false);
    expect(await timingSafeStringEqual("abc", "abcdef")).toBe(false);
    expect(await timingSafeStringEqual("abc", "")).toBe(false);
  });

  it("handles unicode without throwing", async () => {
    expect(await timingSafeStringEqual("tök€n", "tök€n")).toBe(true);
    expect(await timingSafeStringEqual("tök€n", "tok€n")).toBe(false);
  });
});

describe("bearerTokenFrom — Authorization header parsing", () => {
  it("extracts the token from a Bearer header", () => {
    expect(bearerTokenFrom(`Bearer ${TOKEN}`)).toBe(TOKEN);
  });

  it("accepts case-insensitive scheme per RFC 7235", () => {
    expect(bearerTokenFrom(`bearer ${TOKEN}`)).toBe(TOKEN);
    expect(bearerTokenFrom(`BEARER ${TOKEN}`)).toBe(TOKEN);
  });

  it("rejects missing header, other schemes, and empty tokens", () => {
    expect(bearerTokenFrom(null)).toBeNull();
    expect(bearerTokenFrom("")).toBeNull();
    expect(bearerTokenFrom("Basic dXNlcjpwYXNz")).toBeNull();
    expect(bearerTokenFrom("Bearer")).toBeNull();
    expect(bearerTokenFrom("Bearer ")).toBeNull();
    expect(bearerTokenFrom(TOKEN)).toBeNull();
  });
});

describe("isAuthorizedProbeRequest", () => {
  it("authorizes only the exact expected token", async () => {
    expect(await isAuthorizedProbeRequest(`Bearer ${TOKEN}`, TOKEN)).toBe(true);
    expect(await isAuthorizedProbeRequest(`Bearer wrong`, TOKEN)).toBe(false);
    expect(await isAuthorizedProbeRequest(null, TOKEN)).toBe(false);
  });

  it("an unset/empty server token closes the perimeter entirely", async () => {
    expect(await isAuthorizedProbeRequest(`Bearer ${TOKEN}`, undefined)).toBe(false);
    expect(await isAuthorizedProbeRequest(`Bearer `, undefined)).toBe(false);
    expect(await isAuthorizedProbeRequest("Bearer undefined", undefined)).toBe(false);
    expect(await isAuthorizedProbeRequest("Bearer ", "")).toBe(false);
  });
});

describe("isProbeBearerRoute", () => {
  it("matches exactly the enumerated probe routes", () => {
    for (const route of PROBE_BEARER_API_ROUTES) {
      expect(isProbeBearerRoute(route), route).toBe(true);
    }
  });

  it("does NOT match unknown probe-shaped scopes (default-deny)", () => {
    expect(isProbeBearerRoute("/api/probe")).toBe(false);
    expect(isProbeBearerRoute("/api/probe/")).toBe(false);
    expect(isProbeBearerRoute("/api/probe/freshness/extra")).toBe(false);
    expect(isProbeBearerRoute("/api/probe/other")).toBe(false);
    expect(isProbeBearerRoute("/api/probes/freshness")).toBe(false);
  });
});

describe("handleProbeBearerGate", () => {
  it("returns null for non-probe paths (fall through to Clerk)", async () => {
    expect(await handleProbeBearerGate(reqFor("/api/portfolio"), TOKEN)).toBeNull();
    expect(await handleProbeBearerGate(reqFor("/api/probe/other", `Bearer ${TOKEN}`), TOKEN)).toBeNull();
  });

  it("401s a probe request with no Authorization header", async () => {
    const res = await handleProbeBearerGate(reqFor("/api/probe/freshness"), TOKEN);
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
    const body = await res!.json();
    expect(body.code).toBe("UNAUTHORIZED");
    // No detail about WHY (missing vs wrong) — don't help an attacker probe.
    expect(body.detail).toBeUndefined();
    expect(res!.headers.get("Cache-Control")).toContain("no-store");
  });

  it("401s a probe request with the wrong token", async () => {
    const res = await handleProbeBearerGate(
      reqFor("/api/probe/freshness", "Bearer not-the-token"),
      TOKEN,
    );
    expect(res!.status).toBe(401);
  });

  it("401s every probe request when the server token is unset", async () => {
    const res = await handleProbeBearerGate(
      reqFor("/api/probe/freshness", `Bearer ${TOKEN}`),
      undefined,
    );
    expect(res!.status).toBe(401);
  });

  it("passes through (NextResponse.next) with the correct token", async () => {
    const res = await handleProbeBearerGate(
      reqFor("/api/probe/freshness", `Bearer ${TOKEN}`),
      TOKEN,
    );
    expect(res).not.toBeNull();
    expect(res!.status).toBe(200);
    expect(res!.headers.get("x-middleware-next")).toBe("1");
  });
});
