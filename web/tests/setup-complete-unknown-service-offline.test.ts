/**
 * T-445 / R-622 (P1): route-level proof that POST /api/setup/complete fails
 * CLOSED when the FastAPI registry probe is down. With `fetchRegistry()`
 * returning null (radonFetch rejects), a service id that matches
 * SERVICE_PATTERN but is NOT in the KNOWN_SERVICE_IDS mirror must be
 * rejected with 400 BEFORE any credential-store PUT or env-file write.
 * scripts/tests/test_setup_service_id_parity.py only regex-scrapes the
 * mirror; this test drives the real route handler.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";

const radonFetch = vi.fn();
class RadonApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail?: unknown) {
    super(`radon api error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}
vi.mock("@/lib/radonApi", () => ({
  radonFetch,
  RadonApiError,
  radonErrorDetailText: () => "",
}));
vi.mock("@/lib/setup/setupMode", () => ({
  isSetupMode: () => true,
  isAuthMisconfigured: () => false,
}));
const consumeSetupToken = vi.fn();
vi.mock("@/lib/setup/setupToken", () => ({
  setupTokenRejection: () => null,
  consumeSetupToken,
}));
const markSetupComplete = vi.fn(async () => "/repo/.radon/setup-complete");
vi.mock("@/lib/setup/setupComplete", () => ({
  markSetupComplete,
  resolveRepoRoot: () => "/repo",
}));
const writeSetupEnvFiles = vi.fn(async () => [".env"]);
vi.mock("@/lib/setup/envFiles", () => ({
  WEB_ENV_KEYS: new Set(["PUSHOVER_TOKEN"]),
  partitionEnvEncodable: (values: Record<string, string>) => ({ encodable: values, refused: [] }),
  writeSetupEnvFiles,
}));

function post(services: Record<string, unknown>): Promise<Response> {
  return import("../app/api/setup/complete/route").then(({ POST }) =>
    POST(
      new Request("http://localhost/api/setup/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: "t", services }),
      }),
    ),
  );
}

describe("T-445: setup-complete fails closed on unknown service ids when FastAPI is unreachable", () => {
  beforeEach(() => {
    vi.resetModules();
    radonFetch.mockReset();
    // FastAPI DOWN: every probe (GET /credentials included) rejects.
    radonFetch.mockRejectedValue(new Error("ECONNREFUSED"));
    markSetupComplete.mockClear();
    writeSetupEnvFiles.mockClear();
    consumeSetupToken.mockClear();
  });

  it("rejects a SERVICE_PATTERN-valid but unknown id with 400 before any store write", async () => {
    const response = await post({ totally_bogus_svc: { PUSHOVER_TOKEN: "value" } });
    expect(response.status).toBe(400);
    const body = (await response.json()) as { error?: { message?: string }; message?: string };
    expect(JSON.stringify(body)).toContain("totally_bogus_svc is not a credential service");
    // No credential-store PUT ever fired — only the registry GET probe.
    const putCalls = radonFetch.mock.calls.filter(
      ([, init]) => (init as { method?: string } | undefined)?.method === "PUT",
    );
    expect(putCalls).toEqual([]);
    // Nothing reached disk and the setup latch never flipped.
    expect(writeSetupEnvFiles).not.toHaveBeenCalled();
    expect(markSetupComplete).not.toHaveBeenCalled();
    expect(consumeSetupToken).not.toHaveBeenCalled();
  });

  it("still completes the offline path for a KNOWN id (mirror is an allowlist, not a lockout)", async () => {
    const response = await post({ pushover: { PUSHOVER_TOKEN: "value" } });
    expect(response.status).toBe(200);
    const body = (await response.json()) as { ok: boolean; backend: boolean };
    expect(body.ok).toBe(true);
    expect(body.backend).toBe(false);
    expect(writeSetupEnvFiles).toHaveBeenCalledTimes(1);
  });
});
