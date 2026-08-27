/**
 * R5 [P0] — GET /api/performance must not trigger a Flex rebuild on every request.
 *
 * `isPerformanceBehindPortfolioSync` (web/lib/performanceFreshness.ts:65-77)
 * compares the cached payload's `last_sync` / `as_of` against the portfolio
 * snapshot's `last_sync`. Payload v2 emits NEITHER key — it emits `generated_at`
 * and `nav_as_of` — so both sides read `null !== "2026-08-15T13:00:00Z"` and
 * `behindPortfolio` is structurally TRUE for every v2 payload ever written.
 * `shouldRebuild` is therefore always true, and each GET fires
 * `POST /performance/background`, which runs a full Flex SendRequest plus polls
 * against ONE query id.
 *
 * This repo has already taken a 24h -> 168h IBKR throttle embargo from exactly
 * this pattern (project_cash_flow_sync_incident_2026_08_04,
 * feedback_flex_cash_txn_lag). FastAPI's `_running_build` only dedupes
 * CONCURRENT builds; sequential GETs each start a new one.
 *
 * Two independent guards are required (DECISION 7):
 *   1. the freshness comparison learns the v2 keys, so a fresh cache rebuilds 0
 *      times no matter how many GETs arrive;
 *   2. a hard minimum rebuild interval, so a future schema change cannot reopen
 *      the loop even if guard 1 breaks again.
 *
 * Guard 2 is asserted with an upper bound that holds regardless of guard 1, so
 * it keeps biting if someone "fixes" the freshness comparison a third time.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { goldenOkPayload, type PerformancePayloadV2 } from "./fixtures/performanceScenarios";

const mockReadFile = vi.fn();
const mockStat = vi.fn();
vi.mock("fs/promises", () => ({ readFile: mockReadFile, stat: mockStat }));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockGetDb = vi.fn();
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

/** Saturday 2026-08-15, 10:56 ET. Market CLOSED, so the route's cache TTL is
 *  the 60-minute closed-market budget (route.ts:24). */
const NOW = "2026-08-15T14:56:40Z";

/** The portfolio snapshot the operator's page always has alongside performance.
 *  Without it `isPerformanceBehindPortfolioSync` short-circuits to false and
 *  the defect is invisible — which is why every existing route test misses it. */
const PORTFOLIO_LAST_SYNC = "2026-08-15T13:00:00Z";

function mockSnapshots(performance: Record<string, unknown> | null, takenAt: string): void {
  mockGetDb.mockReturnValue({
    execute: vi.fn(async ({ sql }: { sql: string }) => {
      if (/FROM\s+performance_snapshots/i.test(sql)) {
        return { rows: performance ? [{ taken_at: takenAt, payload: JSON.stringify(performance) }] : [] };
      }
      if (/FROM\s+portfolio_snapshots/i.test(sql)) {
        return {
          rows: [
            {
              payload: JSON.stringify({
                last_sync: PORTFOLIO_LAST_SYNC,
                account_summary: { net_liquidation: 1_313_112.03 },
              }),
            },
          ],
        };
      }
      return { rows: [] };
    }),
  });
}

/** A v2 payload generated 60s before NOW — comfortably inside the 60-minute
 *  closed-market TTL, so `perfRead.fresh` is true and nothing about the DATA
 *  justifies a rebuild. */
function freshV2Payload(): PerformancePayloadV2 {
  const payload = goldenOkPayload();
  payload.generated_at = "2026-08-15T14:55:40.320113Z";
  payload.nav_as_of = "2026-08-14";
  payload.period_end = "2026-08-14";
  payload.nav_sessions_behind = 0;
  return payload;
}

/** The same payload generated at 06:00 ET, ~5h before NOW: past the 60-minute
 *  closed-market TTL, so ONE rebuild is correct and expected. */
function staleV2Payload(): PerformancePayloadV2 {
  const payload = freshV2Payload();
  payload.generated_at = "2026-08-15T10:00:00.000000Z";
  return payload;
}

function backgroundRebuildCalls(): unknown[][] {
  return mockRadonFetch.mock.calls.filter((call) => call[0] === "/performance/background");
}

describe("GET /api/performance rebuild loop (R5)", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    vi.clearAllMocks();
    mockReadFile.mockReset();
    mockStat.mockReset();
    mockRadonFetch.mockReset();
    mockGetDb.mockReset();
    // No disk fallback: the DB row is the only cache.
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockRadonFetch.mockResolvedValue({ status: "accepted" });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("1 GET against a fresh v2 cache triggers ZERO background rebuilds", async () => {
    const payload = freshV2Payload();
    mockSnapshots(payload as unknown as Record<string, unknown>, payload.generated_at);

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();

    expect(res.status).toBe(200);
    expect((await res.json()).generated_at).toBe("2026-08-15T14:55:40.320113Z");
    expect(backgroundRebuildCalls()).toHaveLength(0);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("3 sequential GETs against a fresh v2 cache trigger ZERO background rebuilds", async () => {
    const payload = freshV2Payload();
    mockSnapshots(payload as unknown as Record<string, unknown>, payload.generated_at);

    const { GET } = await import("../app/api/performance/route");
    for (let i = 0; i < 3; i += 1) {
      const res = await GET();
      expect(res.status, `GET #${i + 1}`).toBe(200);
    }

    expect(backgroundRebuildCalls()).toHaveLength(0);
  });

  it("a genuinely stale v2 cache rebuilds exactly once, not once per GET", async () => {
    // The minimum-rebuild-interval guard: three GETs seconds apart against the
    // SAME stale row is one rebuild, not three Flex SendRequests. This bound
    // holds even if the v2 freshness comparison regresses again.
    const payload = staleV2Payload();
    mockSnapshots(payload as unknown as Record<string, unknown>, payload.generated_at);

    const { GET } = await import("../app/api/performance/route");
    for (let i = 0; i < 3; i += 1) {
      const res = await GET();
      expect(res.status, `GET #${i + 1}`).toBe(200);
      // SWR: the stale payload is still served immediately every time.
      expect((await res.json()).generated_at, `GET #${i + 1}`).toBe("2026-08-15T10:00:00.000000Z");
      vi.setSystemTime(new Date(Date.now() + 5_000));
    }

    expect(backgroundRebuildCalls()).toHaveLength(0);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("5 GETs can never exceed one rebuild inside the minimum interval, whatever the freshness verdict", async () => {
    // Belt-and-braces for DECISION 7: this bound is deliberately independent of
    // WHY a rebuild was judged necessary, so a schema change that reopens the
    // freshness hole still cannot produce a request storm against one Flex
    // query id.
    const payload = staleV2Payload();
    mockSnapshots(payload as unknown as Record<string, unknown>, payload.generated_at);

    const { GET } = await import("../app/api/performance/route");
    for (let i = 0; i < 5; i += 1) await GET();

    expect(backgroundRebuildCalls().length).toBe(0);
  });
});
