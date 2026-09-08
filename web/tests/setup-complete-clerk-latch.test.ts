/**
 * RC-B1: POST /api/setup/complete used to latch setup completion and consume
 * the one-shot token unconditionally — even when every credential store call
 * failed vendor validation (422) and nothing was written to .env. That left
 * the stack latched out of setup mode with no Clerk keys anywhere and the
 * token burned, so the wizard could never retry.
 *
 * Contract: the latch flips (and the token is consumed) only when the Clerk
 * service actually persisted — stored through FastAPI, or written to .env on
 * the backend-unreachable path. Otherwise the outcomes come back with the
 * token still valid so the wizard retries.
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
const writeSetupEnvFiles = vi.fn(async () => [".env", "web/.env"]);
vi.mock("@/lib/setup/envFiles", () => ({
  WEB_ENV_KEYS: new Set(["CLERK_SECRET_KEY", "PUSHOVER_TOKEN"]),
  partitionEnvEncodable: (values: Record<string, string>) => ({ encodable: values, refused: [] }),
  writeSetupEnvFiles,
}));

const REGISTRY = {
  services: [
    { id: "clerk", fields: [{ name: "CLERK_SECRET_KEY" }] },
    { id: "pushover", fields: [{ name: "PUSHOVER_TOKEN" }] },
  ],
};

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

describe("RC-B1: setup completion latches only after Clerk credentials persist", () => {
  beforeEach(() => {
    vi.resetModules();
    radonFetch.mockReset();
    markSetupComplete.mockClear();
    writeSetupEnvFiles.mockClear();
    consumeSetupToken.mockClear();
  });

  it("every service 422-rejected: no latch, token stays valid, wizard can retry", async () => {
    radonFetch.mockImplementation(async (path: string, init?: { method?: string }) => {
      if (init?.method === "PUT") throw new RadonApiError(422, { message: "vendor rejected" });
      return REGISTRY;
    });
    const response = await post({
      clerk: { CLERK_SECRET_KEY: "sk" },
      pushover: { PUSHOVER_TOKEN: "pt" },
    });
    expect(response.ok).toBe(false);
    const body = (await response.json()) as {
      ok?: boolean;
      outcomes?: Array<{ service: string; stored: boolean }>;
    };
    expect(body.ok).toBe(false);
    expect(body.outcomes?.every((o) => o.stored === false)).toBe(true);
    expect(markSetupComplete).not.toHaveBeenCalled();
    expect(consumeSetupToken).not.toHaveBeenCalled();
    expect(writeSetupEnvFiles).not.toHaveBeenCalled();
  });

  it("clerk stored through FastAPI: latch flips and the token is consumed", async () => {
    radonFetch.mockImplementation(async (path: string, init?: { method?: string }) => {
      if (init?.method === "PUT") return { validation: { status: "ok", message: "" } };
      return REGISTRY;
    });
    const response = await post({ clerk: { CLERK_SECRET_KEY: "sk" } });
    expect(response.status).toBe(200);
    const body = (await response.json()) as { ok: boolean };
    expect(body.ok).toBe(true);
    expect(markSetupComplete).toHaveBeenCalledTimes(1);
    expect(consumeSetupToken).toHaveBeenCalledTimes(1);
  });

  it("backend unreachable: clerk written to .env still latches (offline first-run path)", async () => {
    radonFetch.mockRejectedValue(new Error("ECONNREFUSED"));
    const response = await post({ clerk: { CLERK_SECRET_KEY: "sk" } });
    expect(response.status).toBe(200);
    expect(writeSetupEnvFiles).toHaveBeenCalledTimes(1);
    expect(markSetupComplete).toHaveBeenCalledTimes(1);
    expect(consumeSetupToken).toHaveBeenCalledTimes(1);
  });
});
