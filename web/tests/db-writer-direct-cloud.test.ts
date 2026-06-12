// DUR-07 — scripts/db/writer.js must mirror web/lib/db.ts: the libsql
// embedded replica (retired 2026-05-20) is opt-in ONLY via
// RADON_DB_USE_REPLICA=1, and the legacy RADON_DB_NO_REPLICA kill switch
// still forces it off. Companion to db-direct-cloud.test.ts.
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
  it("does not resolve an embedded-replica config unless explicitly opted in", async () => {
    const writer = await freshWriterModule();
    process.env.NODE_ENV = "development";

    const config = writer.resolveClientConfig();

    expect(config).toEqual({
      url: "https://example.turso.io",
      authToken: "token",
      fetch: expect.any(Function),
    });
  });

  it("resolves the replica config only on explicit opt-in, with a loud warning", async () => {
    const writer = await freshWriterModule();
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    process.env.NODE_ENV = "development";
    process.env.RADON_DB_USE_REPLICA = "1";

    const config = writer.resolveClientConfig();

    expect(config).toEqual(
      expect.objectContaining({
        syncUrl: "libsql://example.turso.io",
        authToken: "token",
      }),
    );
    expect(String(config.url)).toMatch(/^file:.*replica\.db$/);
    const warned = warnSpy.mock.calls.flat().join(" ");
    expect(warned).toContain("RADON_DB_USE_REPLICA");
  });

  it("lets legacy RADON_DB_NO_REPLICA force direct cloud even when both are set", async () => {
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

  it("never resolves a replica config under NODE_ENV=test", async () => {
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
