import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Demo topology auth (approach A): the frontend (Vercel) and the demo VM
 * FastAPI are not on a shared loopback/tailnet, so the backend cannot trust us
 * by origin. radonFetch authenticates as a trusted service with the shared
 * RADON_SERVICE_TOKEN instead of forwarding a per-user JWT.
 *
 * Invariant: the header is attached IFF RADON_SERVICE_TOKEN is set. Prod
 * (app.radon.run, loopback-trusted) never sets it, so prod requests carry no
 * service header — the FastAPI side stays JWT/loopback-gated there.
 *
 * Pairs with scripts/api/tests/test_auth.py::TestIsTrustedServiceRequest.
 */

import { radonFetch } from "../lib/radonApi";

function okJsonResponse() {
  return {
    ok: true,
    status: 200,
    json: async () => ({ ok: true }),
    text: async () => "{}",
  } as unknown as Response;
}

describe("radonFetch — demo service-token header", () => {
  const ORIGINAL = process.env.RADON_SERVICE_TOKEN;
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.fn(async () => okJsonResponse());
    vi.stubGlobal("fetch", fetchSpy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    if (ORIGINAL === undefined) delete process.env.RADON_SERVICE_TOKEN;
    else process.env.RADON_SERVICE_TOKEN = ORIGINAL;
  });

  function headerFromLastCall(): Headers {
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    return init.headers as Headers;
  }

  it("attaches X-Radon-Service-Token when RADON_SERVICE_TOKEN is set", async () => {
    process.env.RADON_SERVICE_TOKEN = "svc-demo-secret";
    await radonFetch("/portfolio");
    expect(headerFromLastCall().get("X-Radon-Service-Token")).toBe("svc-demo-secret");
  });

  it("omits the header when RADON_SERVICE_TOKEN is unset (prod)", async () => {
    delete process.env.RADON_SERVICE_TOKEN;
    await radonFetch("/portfolio");
    expect(headerFromLastCall().get("X-Radon-Service-Token")).toBeNull();
  });
});
