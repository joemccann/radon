import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { isUsTradingDay } from "../lib/serviceHealthWindows";

/**
 * Regression: NAK / RR / MSFT showed wildly wrong Day Chg % on the
 * portfolio surface because /api/previous-close was returning a stale
 * close from Yahoo's `meta.chartPreviousClose` field. Yahoo defines
 * `chartPreviousClose` as "close on the day before the range start" —
 * for `range=5d` that's ~6 trading days ago, not yesterday.
 *
 * The fix walks `indicators.quote[0].close[]` in lock-step with
 * `timestamp[]` and returns the last close whose ET date is strictly
 * before today. See:
 *   - reports/ui-ux-audit-2026-05-20.html § follow-up investigation
 *   - web/app/api/previous-close/route.ts:fetchFromYahoo
 */

// ── Mocks (must precede imports) ──────────────────────────────────────

vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({
    getToken: async () => null,
  })),
}));
vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: vi.fn(async () => ({
    ok: true,
    principal: { userId: "test", kind: "test" },
  })),
}));

// ws — simulate IB always erroring so we exercise the Yahoo path.
vi.mock("ws", () => {
  class FakeWebSocket {
    onError?: () => void;
    onClose?: () => void;
    constructor(_url: string) {
      // Schedule error on next tick so route's setTimeout(3000) never wins.
      setTimeout(() => this.onError?.(), 0);
    }
    on(event: string, cb: () => void) {
      if (event === "error") this.onError = cb;
      if (event === "close") this.onClose = cb;
    }
    send(_payload: string) { /* no-op */ }
    close() { this.onClose?.(); }
  }
  return { WebSocket: FakeWebSocket };
});

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// ── Yahoo payload fixtures ────────────────────────────────────────────

function buildYahooPayload(opts: {
  /** Stale "day before range start" close — must NOT be returned. */
  staleChartPreviousClose: number;
  /** Daily series spanning the last 5 trading days, oldest → newest. */
  dailyCloses: number[];
  /** Timestamps in seconds, lock-step with `dailyCloses`. Last is today. */
  timestamps: number[];
}) {
  return {
    chart: {
      result: [
        {
          meta: { chartPreviousClose: opts.staleChartPreviousClose },
          timestamp: opts.timestamps,
          indicators: { quote: [{ close: opts.dailyCloses }] },
        },
      ],
    },
  };
}

/** Build a 5-entry timestamp array ending TODAY (ET-noon), one day apart. */
function etDateString(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/**
 * Five daily bars whose ET dates are spaced by TRADING days, anchored so
 * index 3 is exactly the session the route resolves to and index 4 is
 * today's still-open bar.
 *
 * The route returns the newest close on or before `previousCloseSessionDate()`.
 * A calendar-day walk put index 3 on a Saturday for any weekend run, so the
 * route correctly skipped past it and returned index 2 — the fixture, not the
 * route, was what failed. Every timestamp is stamped at the 16:00 ET close so
 * its ET date is unambiguous whatever hour the suite runs at.
 */
function buildRecentTimestamps(now: Date = new Date()): number[] {
  const atEtClose = (isoDate: string) =>
    Math.floor(Date.parse(`${isoDate}T20:00:00Z`) / 1000);

  const todayEt = etDateString(now);
  const sessions: string[] = [];
  const cursor = new Date(`${todayEt}T12:00:00Z`);
  while (sessions.length < 4) {
    cursor.setUTCDate(cursor.getUTCDate() - 1);
    const iso = cursor.toISOString().slice(0, 10);
    if (isUsTradingDay(iso)) sessions.unshift(iso);
  }
  return [...sessions.map(atEtClose), atEtClose(todayEt)];
}

// ── Tests ─────────────────────────────────────────────────────────────

describe("/api/previous-close — Yahoo path reads daily close array, not stale meta", () => {
  beforeEach(() => {
    vi.resetModules();
    mockFetch.mockReset();
    delete process.env.UW_TOKEN; // skip UW, exercise Yahoo
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("cache_rollover_at_et_midnight_uses_expected_session", async () => {
    const { previousCloseSessionDate } = await import("../app/api/previous-close/route");
    expect(previousCloseSessionDate(new Date("2026-08-11T03:30:00Z"))).toBe("2026-08-07");
    expect(previousCloseSessionDate(new Date("2026-08-11T04:30:00Z"))).toBe("2026-08-10");
    expect(previousCloseSessionDate(new Date("2026-07-06T16:00:00Z"))).toBe("2026-07-02");
  });

  it("returns the last close strictly before today, ignoring stale chartPreviousClose", async () => {
    const timestamps = buildRecentTimestamps();
    // Yahoo returns: chartPreviousClose=$2.25 (6 days ago, stale) and a daily
    // array with yesterday's real close at index 3 ($1.87). The last entry
    // (index 4) is today's intraday close and must be excluded.
    mockFetch.mockImplementation(async (url: string | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("query1.finance.yahoo.com")) {
        return {
          ok: true,
          json: async () =>
            buildYahooPayload({
              staleChartPreviousClose: 2.25,
              dailyCloses: [2.10, 2.05, 1.95, 1.87, 1.98],
              timestamps,
            }),
        };
      }
      return { ok: false, json: async () => ({}) };
    });

    const { POST } = await import("../app/api/previous-close/route");
    const res = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: ["NAK"] }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    // MUST be 1.87 (yesterday's close), NOT 2.25 (stale meta) or 1.98 (today).
    expect(body.closes.NAK).toBe(1.87);
    expect(body.closes.NAK).not.toBe(2.25);
  });

  it("falls back to regularMarketPreviousClose when daily array is absent", async () => {
    mockFetch.mockImplementation(async (url: string | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("query1.finance.yahoo.com")) {
        return {
          ok: true,
          json: async () => ({
            chart: {
              result: [
                {
                  meta: {
                    chartPreviousClose: 999.99, // stale, must be ignored
                    regularMarketPreviousClose: 417.42,
                  },
                  // no timestamp / indicators block
                },
              ],
            },
          }),
        };
      }
      return { ok: false, json: async () => ({}) };
    });

    const { POST } = await import("../app/api/previous-close/route");
    const res = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: ["MSFT"] }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.closes.MSFT).toBe(417.42);
  });

  it("returns null (no close) when only stale chartPreviousClose is present", async () => {
    // Old behaviour returned chartPreviousClose; new behaviour MUST NOT.
    // A missing close is preferable to a silently wrong one — the UI
    // already renders "---" for missing closes.
    mockFetch.mockImplementation(async (url: string | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("query1.finance.yahoo.com")) {
        return {
          ok: true,
          json: async () => ({
            chart: {
              result: [{ meta: { chartPreviousClose: 175.30 } }],
            },
          }),
        };
      }
      return { ok: false, json: async () => ({}) };
    });

    const { POST } = await import("../app/api/previous-close/route");
    const res = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: ["GHOST"] }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.closes.GHOST).toBeUndefined();
  });

  it("skips bad cells in the daily array (null or zero) and walks back", async () => {
    const timestamps = buildRecentTimestamps();
    mockFetch.mockImplementation(async (url: string | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("query1.finance.yahoo.com")) {
        return {
          ok: true,
          json: async () =>
            buildYahooPayload({
              staleChartPreviousClose: 99,
              // index 4 is today (excluded); index 3 yesterday is null
              // (missing fix); index 2 is day-before-yesterday at $2.05.
              dailyCloses: [2.10, 2.05, 2.05, null as unknown as number, 1.98],
              timestamps,
            }),
        };
      }
      return { ok: false, json: async () => ({}) };
    });

    const { POST } = await import("../app/api/previous-close/route");
    const res = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: ["GAP"] }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.closes.GAP).toBe(2.05);
  });
});
