// scripts/db/writer.js must mirror web/lib/db.ts: the libsql embedded
// replica (retired 2026-05-20) cannot be opened. The DUR-07
// RADON_DB_USE_REPLICA=1 opt-in was removed, so every knob combination
// resolves a direct-to-cloud config. Companion to db-direct-cloud.test.ts.
//
// DUR-09 — the direct-cloud config rewrites libsql:// to https:// (HTTP
// transport, the only one that honours a custom fetch) and installs the
// bounded fetch so every request carries a real timeout. See
// db-writer-bounds.test.ts for the retry/circuit behavior.
//
// NOTE: scripts/ resolves @libsql/client from the ROOT node_modules (web has
// its own copy), so vi.mock on the bare specifier cannot intercept it. We
// pin the decision through the exported pure resolver instead — getDb() is a
// one-line wiring of resolveClientConfig() into createClient().
import { afterEach, describe, expect, it, vi } from "vitest";

const ORIGINAL_NODE_ENV = process.env.NODE_ENV;

async function freshWriterModule() {
  vi.resetModules();
  delete process.env.RADON_DB_USE_REPLICA;
  delete process.env.RADON_DB_NO_REPLICA;
  process.env.TURSO_DB_URL = "libsql://example.turso.io";
  process.env.TURSO_AUTH_TOKEN = "token";
  return import("../../scripts/db/writer.js");
}

afterEach(() => {
  process.env.NODE_ENV = ORIGINAL_NODE_ENV;
  delete process.env.RADON_DB_USE_REPLICA;
  delete process.env.RADON_DB_NO_REPLICA;
  delete process.env.TURSO_DB_URL;
  delete process.env.TURSO_AUTH_TOKEN;
  vi.restoreAllMocks();
});

describe("scripts/db/writer.js direct-cloud default", () => {
  it("resolves a direct-to-cloud config with a clean env", async () => {
    const writer = await freshWriterModule();
    process.env.NODE_ENV = "development";

    const config = writer.resolveClientConfig();

    expect(config).toEqual({
      url: "https://example.turso.io",
      authToken: "token",
      fetch: expect.any(Function),
    });
  });

  it("stays direct-to-cloud when the removed RADON_DB_USE_REPLICA opt-in is set", async () => {
    const writer = await freshWriterModule();
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    process.env.NODE_ENV = "development";
    process.env.RADON_DB_USE_REPLICA = "1";

    const config = writer.resolveClientConfig();

    expect(config).toEqual({
      url: "https://example.turso.io",
      authToken: "token",
      fetch: expect.any(Function),
    });
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it("stays direct-to-cloud with both legacy knobs set", async () => {
    const writer = await freshWriterModule();
    process.env.NODE_ENV = "development";
    process.env.RADON_DB_USE_REPLICA = "1";
    process.env.RADON_DB_NO_REPLICA = "1";

    const config = writer.resolveClientConfig();

    expect(config).toEqual({
      url: "https://example.turso.io",
      authToken: "token",
      fetch: expect.any(Function),
    });
  });

  it("stays direct-to-cloud under NODE_ENV=test", async () => {
    const writer = await freshWriterModule();
    process.env.RADON_DB_USE_REPLICA = "1";

    const config = writer.resolveClientConfig();

    expect(config).toEqual({
      url: "https://example.turso.io",
      authToken: "token",
      fetch: expect.any(Function),
    });
  });
});
