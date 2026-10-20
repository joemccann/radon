/**
 * CSRF defense in depth for mutating Next route handlers.
 *
 * A browser always attaches Sec-Fetch-Site. `same-origin` is the only value
 * that proves the call came from a document this app served. `none` is a
 * user-typed navigation or a bookmark, which cannot be a POST/PUT/PATCH/DELETE
 * from this app, so it is rejected (FastAPI allows `none` only for its
 * trusted-local docs reads). When the header is absent, Origin must be one of
 * the configured first-party origins. Non-browser callers are exempt only by
 * the explicit list pinned below.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NextFetchEvent } from "next/server";
import { NextRequest } from "next/server";

import middleware, {
  PROBE_BEARER_API_ROUTES,
  PUBLIC_WEBHOOK_API_ROUTES,
} from "../middleware";
import { requireRouteAccess } from "@/lib/routeAccess";
import { SETUP_API_PREFIX } from "@/lib/setup/setupMode";
import {
  CROSS_SITE_MUTATION_MESSAGE,
  allowedMutationOrigins,
  decideSameOriginMutation,
  PROBE_BEARER_MUTATION_EXEMPT_PATHS,
  sameOriginMutationResponse,
} from "@/lib/sameOrigin";

const TOKEN = "rpf_test_token_42";

function request(
  url: string,
  method: string,
  headers?: Record<string, string>,
): Request {
  return new Request(url, { method, headers });
}

describe("allowedMutationOrigins", () => {
  it("starts from the first-party origins and extends them from env", () => {
    const origins = allowedMutationOrigins({
      RADON_CORS_EXTRA_ORIGINS: " https://extra.radon.run ,",
      RADON_APP_ORIGIN: "https://app-origin.radon.run",
      DOMAIN: "domain.radon.run",
      VERCEL_URL: "preview.vercel.app",
      RADON_ALLOWED_HOSTS: "tailnet.example.ts.net, *, http://localhost:3999",
    });
    expect(origins).toEqual([
      "http://127.0.0.1:3000",  // pragma: allowlist secret
      "http://[::1]:3000",
      "http://localhost:3000",
      "http://localhost:3999",
      "https://app-origin.radon.run",
      "https://app.radon.run",
      "https://demo.radon.run",
      "https://domain.radon.run",
      "https://extra.radon.run",
      "https://preview.vercel.app",
      "https://tailnet.example.ts.net",
    ]);
  });

  it("does not treat a bare wildcard host as every origin", () => {
    expect(allowedMutationOrigins({ RADON_ALLOWED_HOSTS: "*" })).not.toContain("*");
    expect(allowedMutationOrigins({})).toEqual([
      "http://127.0.0.1:3000",  // pragma: allowlist secret
      "http://[::1]:3000",
      "http://localhost:3000",
      "https://app.radon.run",
      "https://demo.radon.run",
    ]);
  });
});

describe("decideSameOriginMutation", () => {
  it("allows a same-origin mutating request", async () => {
    const decision = await decideSameOriginMutation(
      request("https://app.radon.run/api/orders/place", "POST", {
        "sec-fetch-site": "same-origin",
      }),
      {},
    );
    expect(decision).toEqual({ ok: true, via: "fetch-metadata" });
  });

  it("rejects cross-site and same-site mutating requests", async () => {
    for (const site of ["cross-site", "same-site"]) {
      const decision = await decideSameOriginMutation(
        request("https://app.radon.run/api/orders/place", "POST", {
          "sec-fetch-site": site,
          origin: "https://evil.example",
        }),
        {},
      );
      expect(decision.ok, site).toBe(false);
      if (!decision.ok) {
        expect(decision.status).toBe(403);
        expect(decision.message).toBe(CROSS_SITE_MUTATION_MESSAGE);
      }
    }
  });

  it("rejects Sec-Fetch-Site none because a mutation is not a typed navigation", async () => {
    const decision = await decideSameOriginMutation(
      request("https://app.radon.run/api/orders/place", "DELETE", {
        "sec-fetch-site": "none",
      }),
      {},
    );
    expect(decision.ok).toBe(false);
  });

  it("allows a matching Origin when Sec-Fetch-Site is absent", async () => {
    const decision = await decideSameOriginMutation(
      request("https://app.radon.run/api/orders/place", "PUT", {
        origin: "https://APP.radon.run",
      }),
      {},
    );
    expect(decision).toEqual({ ok: true, via: "origin" });
  });

  it("rejects an Origin that is not a configured app origin", async () => {
    for (const origin of [
      "https://evil.example",
      "https://app.radon.run.evil.example",
      "http://app.radon.run",
    ]) {
      const decision = await decideSameOriginMutation(
        request("https://app.radon.run/api/orders/place", "PATCH", { origin }),
        {},
      );
      expect(decision.ok, origin).toBe(false);
    }
  });

  it("rejects a public mutating request with neither fetch metadata nor Origin", async () => {
    const decision = await decideSameOriginMutation(
      request("https://app.radon.run/api/orders/place", "POST"),
      {},
    );
    expect(decision.ok).toBe(false);
    if (!decision.ok) expect(decision.status).toBe(403);
  });

  it("leaves read-only methods alone", async () => {
    for (const method of ["GET", "HEAD", "OPTIONS"]) {
      const decision = await decideSameOriginMutation(
        request("https://app.radon.run/api/portfolio", method, {
          "sec-fetch-site": "cross-site",
          origin: "https://evil.example",
        }),
        {},
      );
      expect(decision, method).toEqual({ ok: true, via: "read-only" });
    }
  });

  it("exempts webhook, setup, probe bearer, assistant dispatch, loopback, and authless test callers", async () => {
    const cases: Array<{ url: string; headers?: Record<string, string>; exemption: string; env?: Record<string, string> }> = [
      {
        url: "https://app.radon.run/api/webhooks/clerk",
        headers: { "sec-fetch-site": "cross-site", origin: "https://api.clerk.com" },
        exemption: "webhook-clerk",
      },
      {
        url: "https://app.radon.run/api/webhooks/tradingview/path-token",
        headers: { origin: "https://tradingview.example" },
        exemption: "webhook-tradingview",
      },
      {
        url: "https://app.radon.run/api/setup/complete",
        exemption: "setup-token",
      },
      {
        url: "https://app.radon.run/api/probe/freshness",
        headers: { authorization: `Bearer ${TOKEN}`, "sec-fetch-site": "cross-site" },
        exemption: "probe-bearer",
        env: { RADON_PROBE_FRESHNESS_TOKEN: TOKEN },
      },
      {
        url: "https://app.radon.run/api/service-health",
        headers: { authorization: `Bearer ${TOKEN}` },
        exemption: "probe-bearer",
        env: { RADON_PROBE_FRESHNESS_TOKEN: TOKEN },
      },
      {
        url: "http://assistant.local/api/orders/place",
        exemption: "assistant-dispatch",
      },
      {
        url: "http://127.0.0.1:3000/api/orders/place",  // pragma: allowlist secret
        headers: { "x-forwarded-for": "127.0.0.1" },  // pragma: allowlist secret
        exemption: "loopback-direct",
      },
      {
        url: "https://app.radon.run/api/orders/place",
        headers: { "x-radon-authless-test": "secret-token" },
        exemption: "authless-test",
        env: { RADON_AUTHLESS_TEST: "1", RADON_AUTHLESS_TEST_TOKEN: "secret-token" },
      },
    ];
    for (const item of cases) {
      const decision = await decideSameOriginMutation(
        request(item.url, "POST", item.headers),
        item.env ?? {},
      );
      expect(decision, item.exemption).toEqual({
        ok: true,
        via: "exempt",
        exemption: item.exemption,
      });
    }
  });

  it("does not exempt a proxied loopback URL, a wrong probe token, or a foreign fetch site on assistant dispatch", async () => {
    const proxied = await decideSameOriginMutation(
      request("http://127.0.0.1:3000/api/orders/place", "POST", {  // pragma: allowlist secret
        "x-forwarded-for": "203.0.113.5",
      }),
      {},
    );
    expect(proxied.ok).toBe(false);

    const wrongProbe = await decideSameOriginMutation(
      request("https://app.radon.run/api/probe/freshness", "POST", {
        authorization: "Bearer wrong",
      }),
      { RADON_PROBE_FRESHNESS_TOKEN: TOKEN },
    );
    expect(wrongProbe.ok).toBe(false);

    const browserDispatch = await decideSameOriginMutation(
      request("http://assistant.local/api/orders/place", "POST", {
        "sec-fetch-site": "cross-site",
      }),
      {},
    );
    expect(browserDispatch.ok).toBe(false);

    const tradingviewExtra = await decideSameOriginMutation(
      request("https://app.radon.run/api/webhooks/tradingview/token/extra", "POST"),
      {},
    );
    expect(tradingviewExtra.ok).toBe(false);
  });

  it("pins the exempt paths to the middleware webhook and probe lists", () => {
    expect([...PUBLIC_WEBHOOK_API_ROUTES]).toEqual([
      "/api/webhooks/clerk",
      "/api/webhooks/tradingview/[token]",
    ]);
    expect([...PROBE_BEARER_MUTATION_EXEMPT_PATHS]).toEqual([...PROBE_BEARER_API_ROUTES]);
    expect(SETUP_API_PREFIX).toBe("/api/setup/");
  });
});

describe("sameOriginMutationResponse", () => {
  it("returns 403 JSON for a rejected mutation and nothing for a read", async () => {
    const denied = await sameOriginMutationResponse(
      request("https://app.radon.run/api/orders/place", "POST", {
        "sec-fetch-site": "cross-site",
      }),
    );
    expect(denied?.status).toBe(403);
    const body = (await denied?.json()) as { error: string; code: string; requestId: string };
    expect(body.code).toBe("FORBIDDEN");
    expect(body.error).toBe(CROSS_SITE_MUTATION_MESSAGE);
    expect(body.requestId).toBeTruthy();
    expect(denied?.headers.get("cache-control")).toContain("no-store");

    const allowed = await sameOriginMutationResponse(
      request("https://app.radon.run/api/portfolio", "GET", {
        "sec-fetch-site": "cross-site",
      }),
    );
    expect(allowed).toBeNull();
  });
});

describe("requireRouteAccess same-origin gate", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("rejects a cross-site mutation before the handler runs", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const result = await requireRouteAccess(
      request("https://app.radon.run/api/orders/place", "POST", {
        "sec-fetch-site": "cross-site",
      }),
      {},
      {
        authFn: async () => ({ userId: "user-operator" }),
        env: { ALLOWED_USER_IDS: "user-operator" },
      },
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.status).toBe(403);
      const body = (await result.response.json()) as { code: string; error: string };
      expect(body.code).toBe("FORBIDDEN");
      expect(body.error).toBe(CROSS_SITE_MUTATION_MESSAGE);
    }
  });

  it("admits a same-origin mutation for an allowlisted operator", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const result = await requireRouteAccess(
      request("https://app.radon.run/api/orders/place", "POST", {
        "sec-fetch-site": "same-origin",
      }),
      {},
      {
        authFn: async () => ({ userId: "user-operator" }),
        env: { ALLOWED_USER_IDS: "user-operator" },
      },
    );
    expect(result.ok).toBe(true);
  });
});

describe("middleware same-origin short-circuit", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("returns 403 for a cross-site API mutation before Clerk", async () => {
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_dummy");
    vi.stubEnv("CLERK_SECRET_KEY", "sk_test_dummy");
    vi.stubEnv("RADON_SETUP_COMPLETE", "1");
    const response = await middleware(
      new NextRequest("https://app.radon.run/api/orders/place", {
        method: "POST",
        headers: { "sec-fetch-site": "cross-site", origin: "https://evil.example" },
      }),
      {} as NextFetchEvent,
    );
    expect(response?.status).toBe(403);
    const body = (await response?.json()) as { code: string };
    expect(body.code).toBe("FORBIDDEN");
  });
});
