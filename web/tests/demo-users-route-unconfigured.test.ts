/**
 * @vitest-environment node
 *
 * Production topology: the operator is signed in, DEMO_ADMIN_USER_IDS is unset,
 * and the demo DB may be absent. An unconfigured backend must 404 before the
 * admin gate. A configured backend still 403s anyone outside DEMO_ADMIN_USER_IDS.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: "user_operator" })),
  clerkClient: vi.fn(async () => {
    throw new Error("clerk must not be called");
  }),
}));

vi.mock("@/lib/db", () => ({
  getDemoDb: vi.fn(() => {
    throw new Error("demo db must not open");
  }),
}));

beforeEach(() => {
  vi.resetModules();
  delete process.env.TURSO_DEMO_DB_URL;
  delete process.env.DEMO_ADMIN_USER_IDS;
});

afterEach(() => {
  delete process.env.TURSO_DEMO_DB_URL;
  delete process.env.DEMO_ADMIN_USER_IDS;
});

async function loadRoute() {
  return import("../app/api/admin/demo-users/route");
}

describe("demo-users when the demo backend is not configured", () => {
  it("GET returns 404 before the admin gate", async () => {
    const { GET } = await loadRoute();
    const res = await GET();
    expect(res.status).toBe(404);
    await expect(res.json()).resolves.toMatchObject({ error: "Demo backend not configured." });
  });

  it("POST returns 404 before the admin gate", async () => {
    const { POST } = await loadRoute();
    const res = await POST(new Request("http://localhost/api/admin/demo-users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "revoke", userId: "user_trial" }),
    }));
    expect(res.status).toBe(404);
  });
});

describe("demo-users when the demo backend is configured", () => {
  it("GET still 403s a caller who is not a demo admin", async () => {
    process.env.TURSO_DEMO_DB_URL = "libsql://demo.example";
    process.env.DEMO_ADMIN_USER_IDS = "user_other";
    const { GET } = await loadRoute();
    const res = await GET();
    expect(res.status).toBe(403);
  });

  it("POST still 403s a caller who is not a demo admin", async () => {
    process.env.TURSO_DEMO_DB_URL = "libsql://demo.example";
    process.env.DEMO_ADMIN_USER_IDS = "";
    const { POST } = await loadRoute();
    const res = await POST(new Request("http://localhost/api/admin/demo-users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "revoke", userId: "user_trial" }),
    }));
    expect(res.status).toBe(403);
  });
});
