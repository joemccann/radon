import { describe, expect, it, vi } from "vitest";

import {
  createBoundedFetch,
  runNewsfeedMirror,
} from "../db/mirror_newsfeed_to_demo.js";


function post(id = "p1") {
  return {
    id,
    title: "Title",
    content: "Body",
    timestamp: "2026-07-11T12:00:00Z",
    images: "[]",
    raw_images: "[]",
    tags: "[]",
    tags_text: "[]",
    tags_vision: "[]",
    created_at: "2026-07-11T12:00:00Z",
    updated_at: "2026-07-11T12:00:00Z",
  };
}

describe("demo newsfeed mirror reliability", () => {
  it("retries an idempotent transient destination write and records recovery", async () => {
    const src = {
      execute: vi.fn()
        .mockResolvedValueOnce({ rows: [post()] })
        .mockResolvedValue({ rows: [] }),
    };
    const dst = {
      batch: vi.fn()
        .mockRejectedValueOnce(new TypeError("fetch failed"))
        .mockResolvedValueOnce(undefined),
      execute: vi.fn().mockResolvedValue({
        rows: [{ n: 1, newest: "2026-07-11T12:00:00Z" }],
      }),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    const result = await runNewsfeedMirror({
      src,
      dst,
      limit: 400,
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-07-11T12:00:00.000Z",
      runId: "run-1",
    });

    expect(result).toEqual({ mirrored: 1, total: 1 });
    expect(dst.batch).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(src.execute).toHaveBeenLastCalledWith(expect.objectContaining({
      args: expect.arrayContaining(["demo-newsfeed-mirror", "ok"]),
    }));
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "run-1",
      phase: "destination_write",
      event: "retry",
      attempt: 1,
    }));
  });

  it("bounds persistent failures and records an error heartbeat", async () => {
    const src = {
      execute: vi.fn()
        .mockResolvedValueOnce({ rows: [post()] })
        .mockResolvedValue({ rows: [] }),
    };
    const dst = {
      batch: vi.fn().mockRejectedValue(new TypeError("fetch failed")),
      execute: vi.fn(),
    };

    await expect(runNewsfeedMirror({
      src,
      dst,
      limit: 400,
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-07-11T12:00:00.000Z",
      runId: "run-2",
    })).rejects.toThrow("fetch failed");

    expect(dst.batch).toHaveBeenCalledTimes(3);
    expect(src.execute).toHaveBeenLastCalledWith(expect.objectContaining({
      args: expect.arrayContaining(["demo-newsfeed-mirror", "error"]),
    }));
  });

  it("adds an abort deadline without discarding a caller signal", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok"));
    const caller = new AbortController();
    const boundedFetch = createBoundedFetch({ timeoutMs: 1000, fetchImpl });

    await boundedFetch("https://example.test", { signal: caller.signal });

    const init = fetchImpl.mock.calls[0][1];
    expect(init.signal).toBeInstanceOf(AbortSignal);
    caller.abort();
    expect(init.signal.aborted).toBe(true);
  });
});
