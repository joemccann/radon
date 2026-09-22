/**
 * @vitest-environment node
 *
 * A Credentials-tab save must reach the Next.js process without a restart.
 * Next reads UW_TOKEN and the model keys from process.env per request, and
 * web/.env only at boot, so the PUT proxy applies the saved per-request keys
 * to both. Boot-only keys (Clerk, Turso) are never touched live.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

const radonFetch = vi.fn();

vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: vi.fn(async () => ({ ok: true, principal: { userId: "user_op" } })),
}));

vi.mock("@/lib/radonApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/radonApi")>()),
  radonFetch,
}));

const KEYS = ["UW_TOKEN", "XAI_API_KEY", "CLERK_SECRET_KEY"] as const;
let saved: Record<string, string | undefined>;
let repo: string;

beforeEach(async () => {
  vi.resetModules();
  radonFetch.mockReset();
  saved = Object.fromEntries(KEYS.map((k) => [k, process.env[k]]));
  process.env.UW_TOKEN = "old-uw";
  delete process.env.XAI_API_KEY;
  process.env.CLERK_SECRET_KEY = "boot-clerk";
  repo = await fs.mkdtemp(path.join(os.tmpdir(), "radon-live-cred-"));
  await fs.mkdir(path.join(repo, "web"));
  await fs.writeFile(path.join(repo, "web", ".env"), "UW_TOKEN=old-uw\nOTHER=keep\n");
  vi.spyOn(process, "cwd").mockReturnValue(path.join(repo, "web"));
});

afterEach(async () => {
  vi.restoreAllMocks();
  for (const k of KEYS) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
  await fs.rm(repo, { recursive: true, force: true });
});

function put(service: string, values: Record<string, unknown>) {
  return new Request(`http://localhost/api/credentials/${service}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  });
}

describe("PUT /api/credentials/[service] live web env", () => {
  it("applies a stored per-request key to process.env and web/.env", async () => {
    radonFetch.mockResolvedValue({ ok: true });
    const { PUT } = await import("../app/api/credentials/[service]/route");

    const res = await PUT(put("unusual_whales", { UW_TOKEN: "  new-uw  " }) as never, {
      params: Promise.resolve({ service: "unusual_whales" }),
    });

    expect(res.status).toBe(200);
    expect(radonFetch).toHaveBeenCalledWith(
      "/credentials/unusual_whales",
      expect.objectContaining({ method: "PUT" }),
    );
    expect(process.env.UW_TOKEN).toBe("new-uw");
    const webEnv = await fs.readFile(path.join(repo, "web", ".env"), "utf8");
    expect(webEnv).toContain("UW_TOKEN='new-uw'");
    expect(webEnv).not.toContain("old-uw");
    expect(webEnv).toContain("OTHER=keep");
  });

  it("leaves process.env and web/.env alone when FastAPI rejects the save", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    radonFetch.mockRejectedValue(new RadonApiError(422, { code: "CREDENTIAL_REJECTED" }));
    const { PUT } = await import("../app/api/credentials/[service]/route");

    const res = await PUT(put("unusual_whales", { UW_TOKEN: "bad-uw" }) as never, {
      params: Promise.resolve({ service: "unusual_whales" }),
    });

    expect(res.status).toBe(422);
    expect(process.env.UW_TOKEN).toBe("old-uw");
    const webEnv = await fs.readFile(path.join(repo, "web", ".env"), "utf8");
    expect(webEnv).toContain("UW_TOKEN=old-uw");
  });

  it("never applies boot-only keys live", async () => {
    radonFetch.mockResolvedValue({ ok: true });
    const { PUT } = await import("../app/api/credentials/[service]/route");

    await PUT(put("clerk", { CLERK_SECRET_KEY: "new-clerk", XAI_API_KEY: "xai-new" }) as never, {
      params: Promise.resolve({ service: "clerk" }),
    });

    expect(process.env.CLERK_SECRET_KEY).toBe("boot-clerk");
    expect(process.env.XAI_API_KEY).toBe("xai-new");
    const webEnv = await fs.readFile(path.join(repo, "web", ".env"), "utf8");
    expect(webEnv).not.toContain("new-clerk");
  });
});
