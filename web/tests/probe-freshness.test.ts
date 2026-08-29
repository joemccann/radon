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
  RELAY_IDLE_HEARTBEAT_STALE_MS,
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

describe("evaluateRelayTick — a relay that never ticked (R-214)", () => {
  // The relay used to seed `lastTickTimestamp = Date.now()` at module load, so
  // a process that started, connected and received ZERO market data published
  // `last_tick_at = <process start>` with `tick_age_secs ~ 0`. That row was
  // byte-identical to a genuinely live one, and this probe read it as proof of
  // a live data plane. The writer now emits nulls plus `ticks_seen: false`.
  const neverTicked = {
    heartbeat: "tick",
    last_tick_at: null,
    tick_age_secs: null,
    ticks_seen: false,
    active_subscriptions: 12,
    subscribed_symbols: 12,
  };

  it("is not fresh", () => {
    const check = evaluateRelayTick(relayRow({}, neverTicked), "open", OPEN_NOW);
    expect(check.fresh).toBe(false);
  });

  it("reports an unknown age rather than a fabricated one", () => {
    const check = evaluateRelayTick(relayRow({}, neverTicked), "open", OPEN_NOW);
    expect(check.age_secs).toBeNull();
  });

  it("still reads a genuinely fresh relay as fresh", () => {
    expect(evaluateRelayTick(relayRow(), "open", OPEN_NOW).fresh).toBe(true);
  });
});

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
    // Writer-shaped row: the relay heartbeats on the idle path too, so the
    // payload carries subs=0 with an HONEST hour-old tick age and a live
    // updated_at. Pre-2026-08-10 the writer suppressed this heartbeat entirely,
    // which is why the branch below was unreachable in production.
    const idle = relayRow({}, {
      heartbeat: "tick",
      last_tick_at: iso(OPEN_NOW - 3_600_000),
      tick_age_secs: 3600,
      active_subscriptions: 0,
    });
    const result = evaluateRelayTick(idle, "open", OPEN_NOW);
    expect(result.applicable).toBe(true);
    expect(result.fresh).toBe(true);
    expect(result.age_secs).toBe(3600);
  });

  it("holds idle and subscribed relays to the SAME writer-liveness bound", () => {
    // The writer heartbeats every 60s whether or not anyone is subscribed, so an
    // idle relay needs no extra slack to prove it is alive. Two other readers of
    // this same row — serviceHealthWindows "ib-realtime-relay" and
    // scripts/watchdog/services.py — hold it to 5 minutes; letting the idle bound
    // drift wider would make three clocks disagree about one dead process.
    expect(RELAY_IDLE_HEARTBEAT_STALE_MS).toBe(RELAY_HEARTBEAT_STALE_MS);

    const freshnessAtSubs = (active_subscriptions: number) =>
      evaluateRelayTick(
        relayRow(
          { updated_at: iso(OPEN_NOW - RELAY_HEARTBEAT_STALE_MS - 1_000) },
          {
            heartbeat: "tick",
            last_tick_at: iso(OPEN_NOW - 10_000),
            tick_age_secs: 10,
            active_subscriptions,
          },
        ),
        "open",
        OPEN_NOW,
      ).fresh;

    expect(freshnessAtSubs(0)).toBe(false);
    expect(freshnessAtSubs(12)).toBe(false);
  });

  it("overnight close write is not stale inside RELAY_HEARTBEAT_STALE_MS of the 09:30 ET bell", () => {
    // Thu 2026-08-13 09:30:00 ET. Last RTH write was Wed 16:00 ET.
    const bell = Date.parse("2026-08-13T13:30:00Z");
    const yesterdayClose = Date.parse("2026-08-12T20:00:00Z");
    const row = relayRow(
      { updated_at: iso(yesterdayClose) },
      {
        heartbeat: "tick",
        last_tick_at: iso(yesterdayClose - 10_000),
        tick_age_secs: 10,
        active_subscriptions: 0,
      },
    );
    expect(evaluateRelayTick(row, "open", bell).fresh).toBe(true);
    expect(evaluateRelayTick(row, "open", bell + RELAY_HEARTBEAT_STALE_MS - 60_000).fresh).toBe(true);
    expect(evaluateRelayTick(row, "open", bell + RELAY_HEARTBEAT_STALE_MS + 60_000).fresh).toBe(false);
  });

  it("idle relay silent past the idle bound is still a dead relay", () => {
    const dead = relayRow(
      { updated_at: iso(OPEN_NOW - RELAY_IDLE_HEARTBEAT_STALE_MS - 60_000) },
      {
        heartbeat: "tick",
        last_tick_at: iso(OPEN_NOW - 3_600_000),
        tick_age_secs: 3600,
        active_subscriptions: 0,
      },
    );
    expect(evaluateRelayTick(dead, "open", OPEN_NOW).fresh).toBe(false);
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

describe("evaluateScanCheck — gex-scan (scheduled writer)", () => {
  it("is applicable during RTH now that a timer drives it", () => {
    // R-422: `gex-scan` is run by data_refresh's 15-minute RTH driver
    // (`_SCRIPT_SERVICES` maps `gex_scan.py -> "gex-scan"`), so it is a
    // SCHEDULED writer, not an on-demand one. Cataloguing it as on-demand meant
    // check.py never evaluated it and a failing gex_scan was invisible.
    expect(evaluateScanCheck("gex-scan", iso(OPEN_NOW - 60_000), "open", OPEN_NOW).fresh).toBe(true);
    expect(
      evaluateScanCheck("gex-scan", iso(OPEN_NOW - 7 * 86_400_000), "open", OPEN_NOW).fresh,
    ).toBe(false);
  });

  it("is still not applicable while the market is closed", () => {
    // The half that still holds: a closed-market silence is expected.
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
    expect(Object.keys(payload).sort()).toEqual([
      "all_fresh",
      "checks",
      "database_failures",
      "database_ok",
      "generated_at",
      "market_state",
    ]);
    expect(Object.keys(payload.checks).sort()).toEqual(["gex_scan", "journal", "relay_tick", "vcg_scan"]);
    for (const check of Object.values(payload.checks)) {
      expect(Object.keys(check).sort()).toEqual(["age_secs", "applicable", "fresh"]);
    }
    expect(payload.generated_at).toBe(iso(OPEN_NOW));
    expect(payload.market_state).toBe("open");
    expect(payload.database_ok).toBe(true);
    expect(payload.database_failures).toEqual([]);
  });

  it("surfaces database collection failures independently of market applicability", () => {
    const payload = buildFreshnessPayload(
      { ...FRESH_INPUTS, databaseFailures: ["relay_tick", "journal"] },
      new Date(CLOSED_NOW),
    );

    expect(payload.market_state).toBe("closed");
    expect(payload.all_fresh).toBeNull();
    expect(payload.database_ok).toBe(false);
    expect(payload.database_failures).toEqual(["relay_tick", "journal"]);
  });

  it("all_fresh is true when every APPLICABLE check is fresh", () => {
    // gex_scan used to be null-skipped here; it is a scheduled writer now and
    // the fixture supplies a fresh scan time, so it participates. R-422.
    const payload = buildFreshnessPayload(FRESH_INPUTS, new Date(OPEN_NOW));
    expect(payload.checks.gex_scan.applicable).toBe(true);
    expect(payload.checks.gex_scan.fresh).toBe(true);
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
