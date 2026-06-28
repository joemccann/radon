import { afterEach, describe, expect, it, vi } from "vitest";

const createClientMock = vi.fn((config: unknown) => ({ config }));
const ORIGINAL_NODE_ENV = process.env.NODE_ENV;

vi.mock("@libsql/client", () => ({
  createClient: createClientMock,
}));

async function freshDbModule() {
  vi.resetModules();
  createClientMock.mockClear();
  delete process.env.RADON_DB_USE_REPLICA;
  delete process.env.RADON_DB_NO_REPLICA;
  process.env.TURSO_DB_URL = "libsql://example.turso.io";
  process.env.TURSO_AUTH_TOKEN = "token";
  return import("../lib/db");
}

afterEach(() => {
  process.env.NODE_ENV = ORIGINAL_NODE_ENV;
  delete process.env.RADON_DB_USE_REPLICA;
  delete process.env.RADON_DB_NO_REPLICA;
  delete process.env.TURSO_DB_URL;
  delete process.env.TURSO_AUTH_TOKEN;
});

describe("getDb direct-cloud default", () => {
  it("does not open an embedded replica unless explicitly opted in", async () => {
    const db = await freshDbModule();

    db.getDb();

    // Transport normalised to stateless HTTP — no wedge-able WebSocket singleton.
    expect(createClientMock).toHaveBeenCalledWith({
      url: "https://example.turso.io",
      authToken: "token",
    });
  });

  it("allows replica mode only when explicitly enabled and not disabled", async () => {
    const db = await freshDbModule();
    process.env.NODE_ENV = "development";
    process.env.RADON_DB_USE_REPLICA = "1";

    db.getDb();

    expect(createClientMock).toHaveBeenCalledWith(expect.objectContaining({
      syncUrl: "https://example.turso.io",
      authToken: "token",
    }));
  });

  it("lets RADON_DB_NO_REPLICA override the opt-in flag", async () => {
    const db = await freshDbModule();
    process.env.RADON_DB_USE_REPLICA = "1";
    process.env.RADON_DB_NO_REPLICA = "1";

    db.getDb();

    expect(createClientMock).toHaveBeenCalledWith({
      url: "https://example.turso.io",
      authToken: "token",
    });
  });

  it("normalises wss:// and leaves https:// / http:// untouched", async () => {
    const db = await import("../lib/db");
    expect(db.toHttpTransportUrl("libsql://x.turso.io")).toBe("https://x.turso.io");
    expect(db.toHttpTransportUrl("wss://x.turso.io")).toBe("https://x.turso.io");
    expect(db.toHttpTransportUrl("ws://x.turso.io")).toBe("http://x.turso.io");
    expect(db.toHttpTransportUrl("https://x.turso.io")).toBe("https://x.turso.io");
    expect(db.toHttpTransportUrl("http://localhost:8080")).toBe("http://localhost:8080");
  });
});

describe("getDb self-heal", () => {
  it("drops the cached client when an execute rejects, so the next call rebuilds", async () => {
    vi.resetModules();
    createClientMock.mockClear();
    delete process.env.RADON_DB_USE_REPLICA;
    delete process.env.RADON_DB_NO_REPLICA;
    process.env.TURSO_DB_URL = "libsql://example.turso.io";
    process.env.TURSO_AUTH_TOKEN = "token";

    createClientMock.mockImplementation(() => ({
      execute: vi.fn().mockRejectedValue(new Error("connection wedged")),
      batch: vi.fn(),
    }));

    const db = await import("../lib/db");
    const first = db.getDb();
    db.getDb(); // cached — no new client yet
    expect(createClientMock).toHaveBeenCalledTimes(1);

    await expect(first.execute("SELECT 1")).rejects.toThrow("connection wedged");
    // allow the rejection handler to run
    await Promise.resolve();
    await Promise.resolve();

    db.getDb(); // cache was invalidated → rebuild
    expect(createClientMock).toHaveBeenCalledTimes(2);
  });

  it("resetDb() forces a rebuild on the next getDb()", async () => {
    vi.resetModules();
    createClientMock.mockClear();
    createClientMock.mockImplementation((config: unknown) => ({ config, execute: vi.fn() }));
    process.env.TURSO_DB_URL = "libsql://example.turso.io";
    process.env.TURSO_AUTH_TOKEN = "token";

    const db = await import("../lib/db");
    db.getDb();
    db.getDb();
    expect(createClientMock).toHaveBeenCalledTimes(1);
    db.resetDb();
    db.getDb();
    expect(createClientMock).toHaveBeenCalledTimes(2);
  });
});
