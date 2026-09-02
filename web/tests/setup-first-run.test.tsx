/**
 * @vitest-environment jsdom
 *
 * First-run setup surface: mode detection, the middleware collapse-to-/setup
 * gate, the console token, .env materialization, the token-gated API routes
 * (404 outside setup mode is the load-bearing assertion), and the wizard
 * asserted at the wire (full path, method, payload).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";

import { isSetupMode, isSetupPath } from "../lib/setup/setupMode";
import { upsertEnvContent, writeSetupEnvFiles } from "../lib/setup/envFiles";

const CLERK_ENV_KEYS = [
  "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
  "CLERK_SECRET_KEY",
] as const;

let savedEnv: Record<string, string | undefined> = {};

// Shape of FastAPI GET /credentials (scripts/api/routes/credentials.py).
const FASTAPI_REGISTRY = {
  groups: ["Infrastructure", "Market data"],
  services: [
    { id: "clerk", label: "Clerk", group: "Infrastructure", validator: true, slow: false, note: "",
      fields: [{ name: "CLERK_SECRET_KEY" }, { name: "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" }] },
    { id: "unusual_whales", label: "Unusual Whales", group: "Market data", validator: true, slow: false, note: "",
      fields: [{ name: "UW_TOKEN" }] },
  ],
  generated_at: "2026-09-01T00:00:00Z",
};

beforeEach(() => {
  savedEnv = {};
  for (const key of [...CLERK_ENV_KEYS, "RADON_SETUP_TOKEN"]) {
    savedEnv[key] = process.env[key];
    delete process.env[key];
  }
});

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
  for (const [key, value] of Object.entries(savedEnv)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  const tokenModule = await import("../lib/setup/setupToken");
  tokenModule.__resetSetupTokenForTests();
});

describe("setup mode detection", () => {
  it("active only when BOTH Clerk keys are absent", () => {
    expect(isSetupMode(undefined, undefined)).toBe(true);
    expect(isSetupMode("", "  ")).toBe(true);
    expect(isSetupMode("pk_live_x", undefined)).toBe(false);
    expect(isSetupMode(undefined, "sk_live_x")).toBe(false);
  });

  it("setup paths are the page and its API only", () => {
    expect(isSetupPath("/setup")).toBe(true);
    expect(isSetupPath("/api/setup/status")).toBe(true);
    expect(isSetupPath("/")).toBe(false);
    expect(isSetupPath("/api/portfolio")).toBe(false);
    expect(isSetupPath("/setup-adjacent")).toBe(false);
  });
});

describe("middleware setup gate", () => {
  it("in setup mode: pages redirect to /setup, APIs 503, setup paths pass", async () => {
    const { handleSetupModeGate } = await import("../middleware");
    const page = handleSetupModeGate(new NextRequest("http://localhost/orders"));
    expect(page?.status).toBe(307);
    expect(page?.headers.get("location")).toBe("http://localhost/setup");

    const api = handleSetupModeGate(new NextRequest("http://localhost/api/portfolio"));
    expect(api?.status).toBe(503);

    const setupPage = handleSetupModeGate(new NextRequest("http://localhost/setup"));
    expect(setupPage?.status).toBe(200);
  });

  it("with Clerk keys configured the gate is inert", async () => {
    process.env.CLERK_SECRET_KEY = "sk_test_x";
    const { handleSetupModeGate } = await import("../middleware");
    expect(handleSetupModeGate(new NextRequest("http://localhost/orders"))).toBeNull();
  });
});

describe("setup token", () => {
  it("env override wins and verification is exact", async () => {
    process.env.RADON_SETUP_TOKEN = "operator-chosen-token";
    const { verifySetupToken } = await import("../lib/setup/setupToken");
    expect(verifySetupToken("operator-chosen-token")).toBe(true);
    expect(verifySetupToken("wrong")).toBe(false);
    expect(verifySetupToken("")).toBe(false);
    expect(verifySetupToken(42)).toBe(false);
  });

  it("a wrong length is a plain mismatch, never a throw", async () => {
    process.env.RADON_SETUP_TOKEN = "operator-chosen-token";
    const { verifySetupToken } = await import("../lib/setup/setupToken");
    expect(verifySetupToken("x")).toBe(false);
    expect(verifySetupToken("operator-chosen-token-and-more")).toBe(false);
    expect(verifySetupToken("operator-chosen-token")).toBe(true);
  });

  it("locks out after ten wrong tokens in the window; the right token still works before that", async () => {
    process.env.RADON_SETUP_TOKEN = "tok-1";
    const { POST } = await import("../app/api/setup/status/route");
    const attempt = (token: string) =>
      POST(new Request("http://x/api/setup/status", { method: "POST", body: JSON.stringify({ token }) }));
    for (let i = 0; i < 9; i += 1) expect((await attempt("nope")).status).toBe(401);
    expect((await attempt("tok-1")).status).toBe(200);
    expect((await attempt("nope")).status).toBe(401);
    const locked = await attempt("nope");
    expect(locked.status).toBe(429);
    expect(Number(locked.headers.get("Retry-After"))).toBeGreaterThan(0);
    expect((await attempt("tok-1")).status).toBe(429);
  });

  it("generates once and prints to the console", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const { getSetupToken, verifySetupToken, __resetSetupTokenForTests } =
      await import("../lib/setup/setupToken");
    __resetSetupTokenForTests();
    const token = getSetupToken();
    expect(token).toHaveLength(48);
    expect(getSetupToken()).toBe(token);
    expect(verifySetupToken(token)).toBe(true);
    expect(log).toHaveBeenCalledTimes(1);
    log.mockRestore();
  });
});

describe("env materialization", () => {
  it("upsert replaces existing keys in place and appends new ones quoted", () => {
    const existing = "FOO=old\n# comment\nBAR='keep'\n";
    const next = upsertEnvContent(existing, {
      FOO: "new$value",
      BAZ: "fresh",
    });
    expect(next).toContain("FOO='new$value'");
    expect(next).toContain("BAR='keep'");
    expect(next).toContain("BAZ='fresh'");
    expect(next).toContain("# comment");
    expect(next.indexOf("FOO=")).toBeLessThan(next.indexOf("# comment"));
  });

  it("writes root .env with everything and web/.env with the web subset, 0600", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "radon-setup-"));
    await fs.mkdir(path.join(tmp, "web"));
    const written = await writeSetupEnvFiles(
      {
        CLERK_SECRET_KEY: "sk_live_1",
        UW_TOKEN: "uw-1",
        MENTHORQ_PASS: "python-only",
      },
      tmp,
    );
    expect(written).toEqual([path.join(tmp, ".env"), path.join(tmp, "web", ".env")]);
    const root = await fs.readFile(path.join(tmp, ".env"), "utf8");
    expect(root).toContain("MENTHORQ_PASS='python-only'");
    const web = await fs.readFile(path.join(tmp, "web", ".env"), "utf8");
    expect(web).toContain("CLERK_SECRET_KEY='sk_live_1'");
    expect(web).toContain("UW_TOKEN='uw-1'");
    expect(web).not.toContain("MENTHORQ_PASS");
    const mode = (await fs.stat(path.join(tmp, ".env"))).mode & 0o777;
    expect(mode).toBe(0o600);
  });
});

describe("setup API routes", () => {
  it("hard 404 outside setup mode, wrong token 401", async () => {
    process.env.RADON_SETUP_TOKEN = "tok-1";
    process.env.CLERK_SECRET_KEY = "sk_test_x";
    const closed = await import("../app/api/setup/status/route");
    const res404 = await closed.POST(
      new Request("http://x/api/setup/status", {
        method: "POST",
        body: JSON.stringify({ token: "tok-1" }),
      }),
    );
    expect(res404.status).toBe(404);

    delete process.env.CLERK_SECRET_KEY;
    vi.resetModules();
    const open = await import("../app/api/setup/status/route");
    const res401 = await open.POST(
      new Request("http://x/api/setup/status", {
        method: "POST",
        body: JSON.stringify({ token: "nope" }),
      }),
    );
    expect(res401.status).toBe(401);
  });

  it("complete stores per service through FastAPI and writes env files", async () => {
    process.env.RADON_SETUP_TOKEN = "tok-1";
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "radon-setup-"));
    await fs.mkdir(path.join(tmp, "web"));
    const puts: Array<{ path: string; body: unknown }> = [];
    vi.doMock("@/lib/radonApi", () => ({
      radonFetch: vi.fn(async (apiPath: string, init?: { method?: string; body?: string }) => {
        if (init?.method === "GET") return FASTAPI_REGISTRY;
        puts.push({ path: apiPath, body: init?.body ? JSON.parse(init.body) : null });
        return { validation: { status: "valid", message: "" } };
      }),
      RadonApiError: class RadonApiError extends Error {
        status = 500;
        detail: unknown = null;
      },
      radonErrorDetailText: () => "",
    }));
    // Real env-file writer, pointed at the temp repo root.
    vi.doMock("@/lib/setup/envFiles", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../lib/setup/envFiles")>();
      return {
        ...actual,
        writeSetupEnvFiles: (values: Record<string, string>) => actual.writeSetupEnvFiles(values, tmp),
      };
    });
    try {
      const route = await import("../app/api/setup/complete/route");
      const res = await route.POST(
        new Request("http://x/api/setup/complete", {
          method: "POST",
          body: JSON.stringify({
            token: "tok-1",
            services: {
              clerk: { CLERK_SECRET_KEY: "sk_live_1" },
              unusual_whales: { UW_TOKEN: "uw-1" },
            },
          }),
        }),
      );
      expect(res.status).toBe(200);
      const body = (await res.json()) as { outcomes: unknown[]; written: string[] };
      expect(puts.map((p) => p.path).sort()).toEqual([
        "/credentials/clerk",
        "/credentials/unusual_whales",
      ]);
      expect(puts[0].body).toMatchObject({ updated_by: "setup-wizard" });
      expect(body.outcomes).toHaveLength(2);
      expect(body.written.length).toBeGreaterThan(0);
      const root = await fs.readFile(path.join(tmp, ".env"), "utf8");
      expect(root).toContain("UW_TOKEN='uw-1'");
    } finally {
      vi.doUnmock("@/lib/radonApi");
      vi.doUnmock("@/lib/setup/envFiles");
    }
  });
});

describe("setup completion writes only registry-known, backend-accepted values", () => {
  type Reply = { status: number; detail?: unknown } | Record<string, unknown>;
  let replies: Record<string, Reply>;
  let calls: Array<{ path: string; method: string; body: unknown }>;
  const writeSetupEnvFiles = vi.fn(async () => ["/repo/.env"]);

  beforeEach(() => {
    process.env.RADON_SETUP_TOKEN = "tok-1";
    calls = [];
    replies = { "GET /credentials": FASTAPI_REGISTRY };
    writeSetupEnvFiles.mockClear();
    vi.doMock("@/lib/setup/envFiles", async (importOriginal) => ({
      ...(await importOriginal<typeof import("../lib/setup/envFiles")>()),
      writeSetupEnvFiles,
    }));
    vi.doMock("@/lib/radonApi", () => {
      class RadonApiError extends Error {
        constructor(readonly status: number, readonly detail: unknown) {
          super(`Radon API ${status}`);
        }
      }
      return {
        RadonApiError,
        radonErrorDetailText: (detail: unknown) => String(detail),
        radonFetch: vi.fn(async (apiPath: string, init?: { method?: string; body?: string }) => {
          const method = init?.method ?? "GET";
          calls.push({ path: apiPath, method, body: init?.body ? JSON.parse(init.body) : null });
          const reply = replies[`${method} ${apiPath}`];
          if (reply === undefined) throw new TypeError("fetch failed");
          if (typeof reply.status === "number" && "detail" in reply) {
            throw new RadonApiError(reply.status, reply.detail);
          }
          return reply;
        }),
      };
    });
  });

  afterEach(() => {
    vi.doUnmock("@/lib/setup/envFiles");
    vi.doUnmock("@/lib/radonApi");
  });

  async function complete(services: Record<string, unknown>): Promise<Response> {
    const route = await import("../app/api/setup/complete/route");
    return route.POST(
      new Request("http://x/api/setup/complete", {
        method: "POST",
        body: JSON.stringify({ token: "tok-1", services }),
      }),
    );
  }

  it("happy path: PUTs each service and writes exactly the accepted values", async () => {
    replies["PUT /credentials/clerk"] = { validation: { status: "valid", message: "" } };
    replies["PUT /credentials/unusual_whales"] = { validation: { status: "valid", message: "" } };
    const res = await complete({
      clerk: { CLERK_SECRET_KEY: " sk_live_1 " },
      unusual_whales: { UW_TOKEN: "uw-1" },
    });
    expect(res.status).toBe(200);
    expect(calls.filter((c) => c.method === "PUT")).toEqual([
      {
        path: "/credentials/clerk",
        method: "PUT",
        body: { values: { CLERK_SECRET_KEY: "sk_live_1" }, updated_by: "setup-wizard" },
      },
      {
        path: "/credentials/unusual_whales",
        method: "PUT",
        body: { values: { UW_TOKEN: "uw-1" }, updated_by: "setup-wizard" },
      },
    ]);
    expect(writeSetupEnvFiles).toHaveBeenCalledTimes(1);
    expect(writeSetupEnvFiles.mock.calls[0][0]).toEqual({
      CLERK_SECRET_KEY: "sk_live_1",
      UW_TOKEN: "uw-1",
    });
  });

  it("a field the registry does not know is a 400 and nothing is stored or written", async () => {
    replies["PUT /credentials/clerk"] = { validation: { status: "valid", message: "" } };
    const res = await complete({
      clerk: { CLERK_SECRET_KEY: "sk_live_1", PATH: "/tmp/evil" },
    });
    expect(res.status).toBe(400);
    expect(calls.filter((c) => c.method === "PUT")).toEqual([]);
    expect(writeSetupEnvFiles).not.toHaveBeenCalled();
  });

  it("a service the registry does not know is a 400 and nothing is written", async () => {
    const res = await complete({ shell: { LD_PRELOAD: "/tmp/x.so" } });
    expect(res.status).toBe(400);
    expect(calls.filter((c) => c.method === "PUT")).toEqual([]);
    expect(writeSetupEnvFiles).not.toHaveBeenCalled();
  });

  it("a value carrying a newline or control character is a 400 and nothing is written", async () => {
    for (const value of ["sk_live_1\nEVIL=1", "sk_live_1\r", "sk_\u0000live"]) {
      calls = [];
      const res = await complete({ clerk: { CLERK_SECRET_KEY: value } });
      expect(res.status, JSON.stringify(value)).toBe(400);
      expect(calls.filter((c) => c.method === "PUT")).toEqual([]);
    }
    expect(writeSetupEnvFiles).not.toHaveBeenCalled();
  });

  it("a backend 400 is an error outcome, not an offline write", async () => {
    replies["PUT /credentials/clerk"] = { status: 400, detail: "CLERK_SECRET_KEY must be a non-empty string" };
    const res = await complete({ clerk: { CLERK_SECRET_KEY: "sk_live_1" } });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      backend: boolean;
      outcomes: Array<{ stored: boolean; validation: { status: string } }>;
    };
    expect(body.backend).toBe(true);
    expect(body.outcomes).toEqual([
      expect.objectContaining({ service: "clerk", stored: false, validation: expect.objectContaining({ status: "error" }) }),
    ]);
    expect(writeSetupEnvFiles).not.toHaveBeenCalled();
  });

  it("a transport failure still writes registry-known values to .env only", async () => {
    // No PUT reply registered: the mock throws a plain TypeError, the shape of
    // a refused socket, which is the only thing the offline path is for.
    const res = await complete({ clerk: { CLERK_SECRET_KEY: "sk_live_1" } });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { backend: boolean };
    expect(body.backend).toBe(false);
    expect(writeSetupEnvFiles).toHaveBeenCalledTimes(1);
    expect(writeSetupEnvFiles.mock.calls[0][0]).toEqual({ CLERK_SECRET_KEY: "sk_live_1" });
  });

  it("with the registry unreachable only the static web env keys are written", async () => {
    delete replies["GET /credentials"];
    const res = await complete({
      clerk: { CLERK_SECRET_KEY: "sk_live_1" },
      shell: { LD_PRELOAD: "/tmp/x.so" },
    });
    expect(res.status).toBe(400);
    expect(writeSetupEnvFiles).not.toHaveBeenCalled();

    const ok = await complete({ clerk: { CLERK_SECRET_KEY: "sk_live_1" } });
    expect(ok.status).toBe(200);
    expect(writeSetupEnvFiles).toHaveBeenCalledTimes(1);
    expect(writeSetupEnvFiles.mock.calls[0][0]).toEqual({ CLERK_SECRET_KEY: "sk_live_1" });
  });
});

describe("setup wizard wire contract", () => {
  const REGISTRY = {
    ok: true,
    backend: true,
    credentials: {
      groups: ["Infrastructure"],
      services: [
        {
          id: "clerk",
          label: "Clerk",
          group: "Infrastructure",
          validator: true,
          slow: false,
          note: "",
          fields: [
            {
              name: "CLERK_SECRET_KEY",
              label: "Secret key",
              secret: true,
              placeholder: "sk_live_...",
              configured: false,
              hint: "",
              version: 0,
              updated_at: null,
              updated_by: null,
              env_fallback: false,
            },
          ],
        },
      ],
      generated_at: "2026-09-01T00:00:00Z",
    },
  };

  type RecordedCall = { url: string; body: unknown };

  function stubWizardFetch(): {
    calls: RecordedCall[];
    respond: (url: string) => unknown;
  } {
    const calls: RecordedCall[] = [];
    const handler = {
      calls,
      respond: (url: string): unknown => {
        if (url === "/api/setup/status") return REGISTRY;
        if (url === "/api/setup/validate")
          return { validation: { status: "invalid", message: "vendor said no" } };
        if (url === "/api/setup/complete")
          return {
            ok: true,
            backend: true,
            outcomes: [
              { service: "clerk", stored: true, validation: { status: "valid", message: "" } },
            ],
            written: ["/repo/.env", "/repo/web/.env"],
            restart_required: true,
          };
        throw new Error(`unexpected ${url}`);
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        return new Response(JSON.stringify(handler.respond(url)), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    return handler;
  }

  it("token step posts the token, collect step checks and completes on the wire", async () => {
    const { calls } = stubWizardFetch();
    const { default: SetupWizard } = await import("../components/setup/SetupWizard");
    render(<SetupWizard />);

    fireEvent.change(screen.getByTestId("setup-token-input"), {
      target: { value: "tok-abc" },
    });
    fireEvent.click(screen.getByTestId("setup-token-submit"));
    await waitFor(() => expect(screen.getByTestId("setup-service-clerk")).toBeTruthy());
    expect(calls[0]).toEqual({
      url: "/api/setup/status",
      body: { token: "tok-abc" },
    });

    fireEvent.change(document.getElementById("setup-CLERK_SECRET_KEY")!, {
      target: { value: "sk_live_new" },
    });
    fireEvent.click(screen.getByTestId("setup-validate-clerk"));
    await waitFor(() =>
      expect(screen.getByTestId("setup-verdict-clerk").textContent).toMatch(
        /absolutely not/i,
      ),
    );
    const validateCall = calls.find((c) => c.url === "/api/setup/validate")!;
    expect(validateCall.body).toEqual({
      token: "tok-abc",
      service: "clerk",
      values: { CLERK_SECRET_KEY: "sk_live_new" },
    });

    fireEvent.click(screen.getByTestId("setup-complete"));
    await waitFor(() => expect(screen.getByTestId("setup-restart-note")).toBeTruthy());
    const completeCall = calls.find((c) => c.url === "/api/setup/complete")!;
    expect(completeCall.body).toEqual({
      token: "tok-abc",
      services: { clerk: { CLERK_SECRET_KEY: "sk_live_new" } },
    });
    expect(document.body.innerHTML).not.toContain("sk_live_new");
  });
});
