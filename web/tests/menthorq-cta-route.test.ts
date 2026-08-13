import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("fs/promises", () => ({
  readdir: vi.fn(),
  readFile: vi.fn(),
  stat: vi.fn(),
}));

vi.mock("child_process", () => ({
  spawn: vi.fn(() => ({
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    on: vi.fn(),
    unref: vi.fn(),
  })),
}));

import { readFile, readdir, stat } from "fs/promises";
import { spawn } from "child_process";

async function importRoute() {
  return import(`../app/api/menthorq/cta/route?t=${Date.now()}`);
}

describe("GET /api/menthorq/cta", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("returns stale cache metadata and triggers background sync when expected date is newer than latest cache", async () => {
    vi.setSystemTime(new Date("2026-03-12T22:15:00Z"));
    vi.mocked(readdir).mockResolvedValue(["cta_2026-03-11.json"] as never);
    vi.mocked(readFile).mockImplementation(async (path) => {
      const target = String(path);
      if (target.includes("cta-sync.json")) {
        return JSON.stringify({
          service: "cta-sync",
          status: "error",
          trigger: "launchd",
          target_date: "2026-03-12",
          started_at: "2026-03-12T21:35:00Z",
          finished_at: "2026-03-12T21:35:31Z",
          duration_ms: 31_000,
          attempt_count: 2,
          cache_path: null,
          error_type: "auth_rejected",
          error_excerpt: "Your username or password was incorrect",
          artifact_log_path: "logs/cta-sync-artifacts/cta-sync-2026-03-12-attempt-2.log",
        });
      }
      return JSON.stringify({
        date: "2026-03-11",
        fetched_at: "2026-03-12T01:00:00Z",
        tables: { main: [{ underlying: "SPX" }], index: [], commodity: [], currency: [] },
      });
    });
    vi.mocked(stat).mockResolvedValue({ mtimeMs: Date.now() - 60_000 } as never);

    const { GET } = await importRoute();
    const response = await GET();
    const body = await response.json();

    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.target_date).toBe("2026-03-12");
    expect(body.cache_meta.latest_cache_date).toBe("2026-03-11");
    expect(body.cache_meta.stale_reason).toBe("behind_target");
    expect(body.sync_status.status).toBe("error");
    expect(body.sync_status.error_type).toBe("auth_rejected");
    expect(vi.mocked(spawn)).toHaveBeenCalledTimes(1);
  });

  it("returns fresh cache metadata and does not trigger sync when latest cache matches target date", async () => {
    vi.setSystemTime(new Date("2026-03-12T22:15:00Z"));
    vi.mocked(readdir).mockResolvedValue(["cta_2026-03-12.json"] as never);
    vi.mocked(readFile).mockImplementation(async (path) => {
      const target = String(path);
      if (target.includes("cta-sync.json")) {
        return JSON.stringify({
          service: "cta-sync",
          status: "success",
          trigger: "launchd",
          target_date: "2026-03-12",
          started_at: "2026-03-12T21:35:00Z",
          finished_at: "2026-03-12T21:35:31Z",
          duration_ms: 31_000,
          attempt_count: 1,
          cache_path: "data/menthorq_cache/cta_2026-03-12.json",
          error_type: null,
          error_excerpt: null,
          artifact_log_path: null,
        });
      }
      return JSON.stringify({
        date: "2026-03-12",
        fetched_at: "2026-03-12T03:00:00Z",
        tables: { main: [{ underlying: "SPX" }], index: [], commodity: [], currency: [] },
      });
    });
    vi.mocked(stat).mockResolvedValue({ mtimeMs: Date.now() - 30_000 } as never);

    const { GET } = await importRoute();
    const response = await GET();
    const body = await response.json();

    expect(body.cache_meta.is_stale).toBe(false);
    expect(body.cache_meta.target_date).toBe("2026-03-12");
    expect(body.cache_meta.latest_cache_date).toBe("2026-03-12");
    expect(body.cache_meta.stale_reason).toBe("fresh");
    expect(body.sync_status.status).toBe("success");
    expect(vi.mocked(spawn)).not.toHaveBeenCalled();
  });

  it("holiday after close uses the previous trading session", async () => {
    vi.setSystemTime(new Date("2026-07-03T22:15:00Z"));
    vi.mocked(readdir).mockResolvedValue(["cta_2026-07-02.json"] as never);
    vi.mocked(readFile).mockImplementation(async (path) => {
      if (String(path).includes("cta_2026-07-02.json")) {
        return JSON.stringify({
          date: "2026-07-02",
          fetched_at: "2026-07-02T21:30:00Z",
          tables: { main: [{ underlying: "SPX" }], index: [], commodity: [], currency: [] },
        });
      }
      throw new Error("ENOENT");
    });
    vi.mocked(stat).mockResolvedValue({ mtimeMs: Date.now() - 60_000 } as never);

    const { GET } = await importRoute();
    const body = await (await GET()).json();
    expect(body.cache_meta.target_date).toBe("2026-07-02");
    expect(body.cache_meta.is_stale).toBe(false);
    expect(vi.mocked(spawn)).not.toHaveBeenCalled();
  });
});
