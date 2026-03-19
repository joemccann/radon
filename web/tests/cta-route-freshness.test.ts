import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockReaddir = vi.fn();
const mockReadFile = vi.fn();
const mockStat = vi.fn();
const mockMkdir = vi.fn();
const mockSpawn = vi.fn();

vi.mock("fs/promises", () => ({
  readdir: mockReaddir,
  readFile: mockReadFile,
  stat: mockStat,
  mkdir: mockMkdir,
}));

vi.mock("child_process", () => ({
  spawn: mockSpawn,
}));

function makeMtime(ageSeconds: number): Date {
  return new Date(Date.now() - ageSeconds * 1000);
}

function makeCtaPayload(date: string) {
  return {
    date,
    fetched_at: `${date}T21:05:00Z`,
    source: "menthorq_s3_vision",
    tables: {
      main: [
        {
          underlying: "E-Mini S&P 500 Index",
          position_today: 0.45,
          position_yesterday: 0.42,
          position_1m_ago: 0.61,
          percentile_1m: 14,
          percentile_3m: 18,
          percentile_1y: 25,
          z_score_3m: -1.44,
        },
      ],
      index: [],
      commodity: [],
      currency: [],
    },
  };
}

describe("GET /api/menthorq/cta — freshness contract", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-11T22:15:00Z"));

    mockSpawn.mockReturnValue({
      stdout: { on: vi.fn() },
      stderr: { on: vi.fn() },
      on: vi.fn(),
      unref: vi.fn(),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("marks the cache stale when the newest cache file is behind the latest closed trading day and triggers a background sync", async () => {
    mockReaddir.mockResolvedValue(["cta_2026-03-10.json"]);
    mockReadFile.mockImplementation(async (path: string | Buffer | URL) => {
      const target = String(path);
      if (target.includes("cta-sync.json")) {
        throw new Error("ENOENT");
      }
      return JSON.stringify(makeCtaPayload("2026-03-10"));
    });
    mockStat.mockResolvedValue({
      mtimeMs: makeMtime(3_600).getTime(),
      mtime: makeMtime(3_600),
    });

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.date).toBe("2026-03-10");
    expect(body.cache_meta).toMatchObject({
      is_stale: true,
      target_date: "2026-03-11",
      latest_cache_date: "2026-03-10",
      stale_reason: "behind_target",
    });
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(3_595);
    expect(body.sync_status).toBeNull();

    expect(mockSpawn).toHaveBeenCalledOnce();
    const [cmd, args] = mockSpawn.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("bash");
    expect(args.some((arg) => arg.includes("scripts/run_cta_sync.sh"))).toBe(true);
  });

  it("returns a fresh cache_meta contract and does not trigger sync when the latest cache matches the target trading day", async () => {
    mockReaddir.mockResolvedValue(["cta_2026-03-11.json"]);
    mockReadFile.mockImplementation(async (path: string | Buffer | URL) => {
      const target = String(path);
      if (target.includes("cta-sync.json")) {
        return JSON.stringify({
          service: "cta-sync",
          status: "success",
          trigger: "launchd",
          target_date: "2026-03-11",
          started_at: "2026-03-11T22:05:00Z",
          finished_at: "2026-03-11T22:05:31Z",
          duration_ms: 31_000,
          attempt_count: 1,
          cache_path: "data/menthorq_cache/cta_2026-03-11.json",
          error_type: null,
          error_excerpt: null,
          artifact_log_path: null,
        });
      }
      return JSON.stringify(makeCtaPayload("2026-03-11"));
    });
    mockStat.mockResolvedValue({
      mtimeMs: makeMtime(120).getTime(),
      mtime: makeMtime(120),
    });

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.cache_meta).toMatchObject({
      is_stale: false,
      target_date: "2026-03-11",
      latest_cache_date: "2026-03-11",
      stale_reason: "fresh",
    });
    expect(body.sync_status).toMatchObject({
      status: "success",
      target_date: "2026-03-11",
    });
    expect(mockSpawn).not.toHaveBeenCalled();
  });

  it("ignores a stale sync health target_date that is ahead of the computed trading day", async () => {
    // Bug reproduction: sync health had target_date "2026-03-12" from a previous
    // bad run, but the actual latest closed trading day is "2026-03-11".
    // The route must use the computed date, not the stale sync health override.
    mockReaddir.mockResolvedValue(["cta_2026-03-10.json"]);
    mockReadFile.mockImplementation(async (path: string | Buffer | URL) => {
      const target = String(path);
      if (target.includes("cta-sync.json")) {
        return JSON.stringify({
          service: "cta-sync",
          state: "degraded",
          target_date: "2026-03-12",
          latest_available_date: "2026-03-10",
          last_attempt_started_at: "2026-03-12T02:55:58Z",
          last_error: { type: "unexpected_failure", message: "S3 download failed" },
          attempt_count: 1,
        });
      }
      return JSON.stringify(makeCtaPayload("2026-03-10"));
    });
    mockStat.mockResolvedValue({
      mtimeMs: makeMtime(86_400).getTime(),
      mtime: makeMtime(86_400),
    });

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    // Key assertion: target_date must be the computed "2026-03-11", NOT the
    // stale sync health "2026-03-12"
    expect(body.cache_meta.target_date).toBe("2026-03-11");
    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.stale_reason).toBe("behind_target");
    expect(mockSpawn).toHaveBeenCalledOnce();
    // Background sync should use the correct date
    const [, args] = mockSpawn.mock.calls[0] as [string, string[]];
    expect(args).toContain("2026-03-11");
  });

  it("returns a degraded 503 response with stale metadata when no CTA cache exists yet", async () => {
    mockReaddir.mockResolvedValue([]);
    mockReadFile.mockRejectedValue(new Error("ENOENT"));

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(503);
    expect(body.date).toBeNull();
    expect(body.tables).toBeNull();
    expect(body.cache_meta).toMatchObject({
      is_stale: true,
      target_date: "2026-03-11",
      latest_cache_date: null,
      stale_reason: "missing_cache",
    });
    expect(mockSpawn).toHaveBeenCalledOnce();
  });
});
