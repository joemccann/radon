// DUR-09 — scripts/db/writer.js must put real bounds on the Turso protocol
// layer: per-request fetch timeout, 2 retries with backoff on transport
// errors, and an in-process circuit breaker (open after N consecutive failed
// operations, cooldown, loud transition logs). Companion to
// db-writer-direct-cloud.test.ts.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Writer = typeof import("../../scripts/db/writer.js");

let writer: Writer;

function transportError(message = "fetch failed"): Error {
  // undici surfaces network failures as TypeError("fetch failed").
  const err = new TypeError(message);
  return err;
}

function timeoutError(): Error {
  const err = new Error("The operation was aborted due to timeout");
  err.name = "TimeoutError";
  return err;
}

function sqlError(): Error {
  const err = new Error("UNIQUE constraint failed: posts.id") as Error & { code: string };
  err.code = "SQLITE_CONSTRAINT_PRIMARYKEY";
  return err;
}

function fakeClient(execute: ReturnType<typeof vi.fn>) {
  return { execute, batch: vi.fn(async () => []) } as unknown as Parameters<
    Writer["__setDbForTests"]
  >[0];
}

beforeEach(async () => {
  vi.resetModules();
  writer = await import("../../scripts/db/writer.js");
  writer.__resetDbForTests();
  vi.useFakeTimers();
});

afterEach(() => {
  writer.__resetDbForTests();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("retry with backoff on transport errors", () => {
  it("retries twice then succeeds (3 attempts total)", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const execute = vi
      .fn()
      .mockRejectedValueOnce(timeoutError())
      .mockRejectedValueOnce(transportError())
      .mockResolvedValueOnce({ rowsAffected: 1 });
    writer.__setDbForTests(fakeClient(execute));

    const op = writer.recordServiceHealth("newsfeed-scraper", "ok");
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(op).resolves.toBeUndefined();
    expect(execute).toHaveBeenCalledTimes(3);
  });

  it("gives up after 2 retries and rethrows the transport error", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    const execute = vi.fn().mockRejectedValue(timeoutError());
    writer.__setDbForTests(fakeClient(execute));

    const op = writer.recordServiceHealth("newsfeed-scraper", "ok");
    op.catch(() => {});
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(op).rejects.toThrow(/timeout/i);
    expect(execute).toHaveBeenCalledTimes(3);
  });

  it("does NOT retry SQL/constraint errors", async () => {
    const execute = vi.fn().mockRejectedValue(sqlError());
    writer.__setDbForTests(fakeClient(execute));

    await expect(
      writer.recordServiceHealth("newsfeed-scraper", "ok"),
    ).rejects.toThrow(/UNIQUE constraint/);
    expect(execute).toHaveBeenCalledTimes(1);
  });
});

describe("circuit breaker", () => {
  it("opens after N consecutive failed operations and fails fast without touching Turso", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    writer.__setDbBoundsForTests({ retryBackoffMs: [], circuitOpenThreshold: 2 });
    const execute = vi.fn().mockRejectedValue(transportError());
    writer.__setDbForTests(fakeClient(execute));

    await expect(writer.recordServiceHealth("s", "ok")).rejects.toThrow();
    await expect(writer.recordServiceHealth("s", "ok")).rejects.toThrow();
    expect(execute).toHaveBeenCalledTimes(2);

    // Third op: circuit is open — rejected immediately, client untouched.
    await expect(writer.recordServiceHealth("s", "ok")).rejects.toThrow(
      writer.DbCircuitOpenError,
    );
    expect(execute).toHaveBeenCalledTimes(2);

    const logged = errorSpy.mock.calls.flat().join(" ");
    expect(logged).toContain("circuit OPEN");
  });

  it("half-opens after the cooldown and closes on success, loudly", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    writer.__setDbBoundsForTests({
      retryBackoffMs: [],
      circuitOpenThreshold: 1,
      circuitCooldownMs: 60_000,
    });
    const execute = vi.fn().mockRejectedValueOnce(transportError()).mockResolvedValue({
      rowsAffected: 1,
    });
    writer.__setDbForTests(fakeClient(execute));

    await expect(writer.recordServiceHealth("s", "ok")).rejects.toThrow();
    await expect(writer.recordServiceHealth("s", "ok")).rejects.toThrow(
      writer.DbCircuitOpenError,
    );

    // Cooldown elapses → next op probes Turso again and recovers.
    await vi.advanceTimersByTimeAsync(60_001);
    await expect(writer.recordServiceHealth("s", "ok")).resolves.toBeUndefined();
    expect(execute).toHaveBeenCalledTimes(2);

    const logged = errorSpy.mock.calls.flat().join(" ");
    expect(logged).toContain("circuit CLOSED");

    // Circuit stays closed for subsequent ops.
    await expect(writer.recordServiceHealth("s", "ok")).resolves.toBeUndefined();
    expect(execute).toHaveBeenCalledTimes(3);
  });

  it("SQL errors do not count toward the circuit", async () => {
    writer.__setDbBoundsForTests({ retryBackoffMs: [], circuitOpenThreshold: 1 });
    const execute = vi.fn().mockRejectedValue(sqlError());
    writer.__setDbForTests(fakeClient(execute));

    await expect(writer.recordServiceHealth("s", "ok")).rejects.toThrow(/UNIQUE/);
    // A transport-healthy circuit means the next op still reaches the client.
    await expect(writer.recordServiceHealth("s", "ok")).rejects.toThrow(/UNIQUE/);
    expect(execute).toHaveBeenCalledTimes(2);
  });
});

describe("per-request fetch bound", () => {
  it("resolveClientConfig converts libsql:// to https:// and installs a bounded fetch", async () => {
    process.env.TURSO_DB_URL = "libsql://example.turso.io";
    process.env.TURSO_AUTH_TOKEN = "token";
    try {
      const config = writer.resolveClientConfig();
      expect(config.url).toBe("https://example.turso.io");
      expect(config.authToken).toBe("token");
      expect(typeof config.fetch).toBe("function");
    } finally {
      delete process.env.TURSO_DB_URL;
      delete process.env.TURSO_AUTH_TOKEN;
    }
  });

  it("the bounded fetch passes an AbortSignal so a hung socket cannot wait forever", async () => {
    const seen: { signal?: unknown } = {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: unknown, init?: { signal?: unknown }) => {
        seen.signal = init?.signal;
        return { ok: true } as Response;
      }),
    );
    process.env.TURSO_DB_URL = "libsql://example.turso.io";
    process.env.TURSO_AUTH_TOKEN = "token";
    try {
      vi.useRealTimers(); // AbortSignal.timeout needs real timers
      const config = writer.resolveClientConfig();
      await (config.fetch as (input: unknown, init?: unknown) => Promise<unknown>)(
        "https://example.turso.io/v2/pipeline",
        {},
      );
      expect(seen.signal).toBeInstanceOf(AbortSignal);
    } finally {
      delete process.env.TURSO_DB_URL;
      delete process.env.TURSO_AUTH_TOKEN;
    }
  });
});
