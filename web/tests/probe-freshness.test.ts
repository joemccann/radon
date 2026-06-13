/**
 * DUR-16: pure evaluation matrix for the /api/probe/freshness checks.
 *
 * Applicability is market-state-driven via the serviceHealthWindows
 * machinery (never a uniform staleness model — feedback_service_health_staleness,
 * feedback_extended_market_state_window):
 *
 *   relay_tick — RTH only; tick age from the relay's service_health
 *                heartbeat detail (last_tick_at), dead-relay detected via
 *                the heartbeat row's own updated_at.
 *   vcg_scan   — scheduled RTH cadence; fresh within its `open` window.
 *   gex_scan   — ON-DEMAND writer today: quiet is expected at any hour, so
 *                it is never applicable (fresh=null). Flips automatically
 *                if its category in SERVICE_FRESHNESS_WINDOWS changes.
 *   journal    — fills are only expected during RTH; the newest journal
 *                row must be within the weekend-covering window.
 *
 * all_fresh = AND over applicable checks; null when none are applicable.
 */
import { describe, expect, it } from "vitest";

import {
  buildFreshnessPayload,
  evaluateJournalCheck,
  evaluateRelayTick,
  evaluateScanCheck,
  JOURNAL_FRESH_WINDOW_MS,
  RELAY_HEARTBEAT_STALE_MS,
  RELAY_TICK_FRESH_SECS,
  type RelayHealthRow,
} from "../lib/probeFreshness";

// Wednesday 2026-06-10 11:00 ET (EDT) — regular trading hours.
const OPEN_NOW = Date.parse("2026-06-10T15:00:00Z");
// Wednesday 2026-06-10 08:00 ET — pre-market (extended).
const EXTENDED_NOW = Date.parse("2026-06-10T12:00:00Z");
// Sunday 2026-06-07 — closed.
const CLOSED_NOW = Date.parse("2026-06-07T15:00:00Z");

const NOT_APPLICABLE = { applicable: false, age_secs: null, fresh: null };

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function relayRow(overrides: Partial<RelayHealthRow> = {}, detail?: Record<string, unknown> | null): RelayHealthRow {
  const detailJson =
    detail === null
      ? null
      : JSON.stringify(
          detail ?? {
            heartbeat: "tick",
            last_tick_at: iso(OPEN_NOW - 10_000),
            tick_age_secs: 10,
            active_subscriptions: 12,
          },
        );
  return {
    state: "ok",
    last_error: detailJson,
    updated_at: iso(OPEN_NOW - 30_000),
    ...overrides,
  };
}

describe("evaluateRelayTick", () => {
  it("is not applicable outside RTH", () => {
    expect(evaluateRelayTick(relayRow(), "extended", EXTENDED_NOW)).toEqual(NOT_APPLICABLE);
    expect(evaluateRelayTick(relayRow(), "closed", CLOSED_NOW)).toEqual(NOT_APPLICABLE);
  });

  it("fresh during RTH with a recent tick and live heartbeat", () => {
    expect(evaluateRelayTick(relayRow(), "open", OPEN_NOW)).toEqual({
      applicable: true,
      age_secs: 10,
      fresh: true,
    });
  });

  it("not fresh when the tick is older than the threshold with active demand", () => {
    const staleTick = relayRow({}, {
      last_tick_at: iso(OPEN_NOW - (RELAY_TICK_FRESH_SECS + 60) * 1000),
      active_subscriptions: 8,
    });
    const result = evaluateRelayTick(staleTick, "open", OPEN_NOW);
    expect(result.applicable).toBe(true);
    expect(result.age_secs).toBe(RELAY_TICK_FRESH_SECS + 60);
    expect(result.fresh).toBe(false);
  });

  it("not fresh when the relay row is in error state, regardless of tick age", () => {
    const result = evaluateRelayTick(relayRow({ state: "error" }), "open", OPEN_NOW);
    expect(result.fresh).toBe(false);
    expect(result.applicable).toBe(true);
  });

  it("not fresh when the heartbeat itself has gone silent (dead relay)", () => {
    const silent = relayRow({ updated_at: iso(OPEN_NOW - RELAY_HEARTBEAT_STALE_MS - 60_000) });
    expect(evaluateRelayTick(silent, "open", OPEN_NOW).fresh).toBe(false);
  });

  it("fresh with zero active subscriptions — no demand means no ticks expected", () => {
    const idle = relayRow({}, {
      last_tick_at: iso(OPEN_NOW - 3_600_000),
      active_subscriptions: 0,
    });
    const result = evaluateRelayTick(idle, "open", OPEN_NOW);
    expect(result.applicable).toBe(true);
    expect(result.fresh).toBe(true);
    expect(result.age_secs).toBe(3600);
  });

  it("missing row or unparsable detail during RTH proves nothing — not fresh", () => {
    expect(evaluateRelayTick(null, "open", OPEN_NOW)).toEqual({
      applicable: true,
      age_secs: null,
      fresh: false,
    });
    const garbage = relayRow({ last_error: "not json{" });
    expect(evaluateRelayTick(garbage, "open", OPEN_NOW)).toEqual({
      applicable: true,
      age_secs: null,
      fresh: false,
    });
    const noDetail = relayRow({ last_error: null });
    expect(evaluateRelayTick(noDetail, "open", OPEN_NOW).fresh).toBe(false);
  });
});

describe("evaluateScanCheck — vcg-scan (scheduled RTH cadence)", () => {
  it("is not applicable outside RTH", () => {
    expect(evaluateScanCheck("vcg-scan", iso(EXTENDED_NOW - 60_000), "extended", EXTENDED_NOW)).toEqual(NOT_APPLICABLE);
    expect(evaluateScanCheck("vcg-scan", iso(CLOSED_NOW - 60_000), "closed", CLOSED_NOW)).toEqual(NOT_APPLICABLE);
  });

  it("fresh within the 15-minute open window", () => {
    const result = evaluateScanCheck("vcg-scan", iso(OPEN_NOW - 5 * 60_000), "open", OPEN_NOW);
    expect(result).toEqual({ applicable: true, age_secs: 300, fresh: true });
  });

  it("not fresh past the open window", () => {
    const result = evaluateScanCheck("vcg-scan", iso(OPEN_NOW - 20 * 60_000), "open", OPEN_NOW);
    expect(result.fresh).toBe(false);
    expect(result.age_secs).toBe(1200);
  });

  it("treats naive Python isoformat scan_time as UTC (parseScanTime contract)", () => {
    // 14:55:00 naive == 14:55:00Z — 5 minutes before OPEN_NOW, i.e. fresh.
    const result = evaluateScanCheck("vcg-scan", "2026-06-10T14:55:00.123456", "open", OPEN_NOW);
    expect(result.fresh).toBe(true);
    expect(result.age_secs).toBe(300);
  });

  it("missing snapshot during RTH proves nothing — not fresh", () => {
    expect(evaluateScanCheck("vcg-scan", null, "open", OPEN_NOW)).toEqual({
      applicable: true,
      age_secs: null,
      fresh: false,
    });
  });
});

describe("evaluateScanCheck — gex-scan (on-demand writer)", () => {
  it("is never applicable while its category is on-demand — quiet is expected", () => {
    // feedback_service_health_staleness: past-window silence on an on-demand
    // writer means "nobody has looked", never an outage. Asserting freshness
    // would burn the scan SLO on user inactivity.
    expect(evaluateScanCheck("gex-scan", iso(OPEN_NOW - 60_000), "open", OPEN_NOW)).toEqual(NOT_APPLICABLE);
    expect(evaluateScanCheck("gex-scan", iso(OPEN_NOW - 7 * 86_400_000), "open", OPEN_NOW)).toEqual(NOT_APPLICABLE);
    expect(evaluateScanCheck("gex-scan", null, "closed", CLOSED_NOW)).toEqual(NOT_APPLICABLE);
  });
});

describe("evaluateJournalCheck", () => {
  it("is not applicable outside RTH (fills only expected while the market trades)", () => {
    expect(evaluateJournalCheck(iso(CLOSED_NOW - 60_000), "closed", CLOSED_NOW)).toEqual(NOT_APPLICABLE);
    expect(evaluateJournalCheck(iso(EXTENDED_NOW - 60_000), "extended", EXTENDED_NOW)).toEqual(NOT_APPLICABLE);
  });

  it("fresh when the newest row is inside the weekend-covering window", () => {
    const result = evaluateJournalCheck(iso(OPEN_NOW - 3_600_000), "open", OPEN_NOW);
    expect(result).toEqual({ applicable: true, age_secs: 3600, fresh: true });
  });

  it("not fresh when the newest row is older than the window", () => {
    const tooOld = iso(OPEN_NOW - JOURNAL_FRESH_WINDOW_MS - 3_600_000);
    expect(evaluateJournalCheck(tooOld, "open", OPEN_NOW).fresh).toBe(false);
  });

  it("an empty journal during RTH proves nothing — not fresh", () => {
    expect(evaluateJournalCheck(null, "open", OPEN_NOW)).toEqual({
      applicable: true,
      age_secs: null,
      fresh: false,
    });
  });
});

describe("buildFreshnessPayload", () => {
  const FRESH_INPUTS = {
    relayRow: relayRow(),
    vcgScanTime: iso(OPEN_NOW - 5 * 60_000),
    gexScanTime: iso(OPEN_NOW - 5 * 60_000),
    journalWrittenAt: iso(OPEN_NOW - 3_600_000),
  };

  it("matches the fixed contract shape", () => {
    const payload = buildFreshnessPayload(FRESH_INPUTS, new Date(OPEN_NOW));
    expect(Object.keys(payload).sort()).toEqual(["all_fresh", "checks", "generated_at", "market_state"]);
    expect(Object.keys(payload.checks).sort()).toEqual(["gex_scan", "journal", "relay_tick", "vcg_scan"]);
    for (const check of Object.values(payload.checks)) {
      expect(Object.keys(check).sort()).toEqual(["age_secs", "applicable", "fresh"]);
    }
    expect(payload.generated_at).toBe(iso(OPEN_NOW));
    expect(payload.market_state).toBe("open");
  });

  it("all_fresh is true when every APPLICABLE check is fresh (gex null-skipped)", () => {
    const payload = buildFreshnessPayload(FRESH_INPUTS, new Date(OPEN_NOW));
    expect(payload.checks.gex_scan.applicable).toBe(false);
    expect(payload.all_fresh).toBe(true);
  });

  it("all_fresh is false when one applicable check fails", () => {
    const payload = buildFreshnessPayload(
      { ...FRESH_INPUTS, vcgScanTime: iso(OPEN_NOW - 60 * 60_000) },
      new Date(OPEN_NOW),
    );
    expect(payload.checks.vcg_scan.fresh).toBe(false);
    expect(payload.all_fresh).toBe(false);
  });

  it("all_fresh is null when no check is applicable (closed market)", () => {
    const payload = buildFreshnessPayload(FRESH_INPUTS, new Date(CLOSED_NOW));
    expect(payload.market_state).toBe("closed");
    for (const check of Object.values(payload.checks)) {
      expect(check).toEqual(NOT_APPLICABLE);
    }
    expect(payload.all_fresh).toBeNull();
  });
});
