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
 *   gex_scan   - scheduled on the 15-minute refresh driver. Fresh inside
 *                the 35-minute open window (one missed fire). Not applicable
 *                while the market is closed.
 *   journal    — RTH only. journal-sync is stale only after it has not
 *                been ok for longer than its open window (one error cycle
 *                does not page). journal-gap-sli pages only when a fresh
 *                row reports missing_exec_id_count>0. A gap-sli self-error
 *                or a stale gap-sli row is a warning. Last-fill age does
 *                not page. A missing row is stale.
 *
 * all_fresh = AND over applicable checks; null when none are applicable.
 */
import { describe, expect, it } from "vitest";

import { getFreshnessWindowMs } from "../lib/serviceHealthWindows";

import {
  buildFreshnessPayload,
  evaluateJournalCheck,
  evaluateRelayTick,
  evaluateScanCheck,
  RELAY_HEARTBEAT_STALE_MS,
  RELAY_IDLE_HEARTBEAT_STALE_MS,
  RELAY_TICK_FRESH_SECS,
  type JournalHealthRow,
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

  it("keeps one missed 15-minute cycle fresh and pages the one after that", () => {
    // Page eeef09595a01893b4e31abd5cbda352e: 23m46s after the last ok scan.
    // The open window has to match the 15-minute driver (35m), not vcg (15m).
    const oneMiss = evaluateScanCheck("gex-scan", iso(OPEN_NOW - (23 * 60 + 46) * 1000), "open", OPEN_NOW);
    expect(oneMiss.fresh).toBe(true);
    expect(oneMiss.age_secs).toBe(23 * 60 + 46);
    const twoMisses = evaluateScanCheck(
      "gex-scan",
      iso(OPEN_NOW - (38 * 60 + 46) * 1000),
      "open",
      OPEN_NOW,
    );
    expect(twoMisses.fresh).toBe(false);
    expect(twoMisses.age_secs).toBe(38 * 60 + 46);
  });

  it("is still not applicable while the market is closed", () => {
    // The half that still holds: a closed-market silence is expected.
    expect(evaluateScanCheck("gex-scan", null, "closed", CLOSED_NOW)).toEqual(NOT_APPLICABLE);
  });
});

function pipelineRows(
  nowMs: number,
  overrides: {
    syncAt?: string | null;
    syncState?: string;
    /** Start of the current non-ok streak. service_health_events.created_at. */
    notOkSince?: string | null;
    gapAt?: string | null;
    gapState?: string;
    missing?: number | null;
    gapError?: string | null;
  } = {},
): { journalSync: JournalHealthRow; journalGapSli: JournalHealthRow } {
  const missing = overrides.missing === undefined ? 0 : overrides.missing;
  const gapError = overrides.gapError;
  return {
    journalSync: {
      state: overrides.syncState ?? "ok",
      last_error: null,
      updated_at: overrides.syncAt === undefined ? iso(nowMs - 30_000) : overrides.syncAt,
      notOkSince: overrides.notOkSince === undefined ? null : overrides.notOkSince,
    },
    journalGapSli: {
      state: overrides.gapState ?? "ok",
      last_error:
        gapError !== undefined
          ? gapError
          : missing === null
            ? null
            : JSON.stringify({ missing_exec_id_count: missing }),
      updated_at: overrides.gapAt === undefined ? iso(nowMs - 60_000) : overrides.gapAt,
    },
  };
}

function freshnessAt(
  nowMs: number,
  journalWrittenAt: string | null,
  pipe: { journalSync: JournalHealthRow | null; journalGapSli: JournalHealthRow | null } = pipelineRows(nowMs),
) {
  return {
    relayRow: relayRow(
      { updated_at: iso(nowMs - 30_000) },
      {
        heartbeat: "tick",
        last_tick_at: iso(nowMs - 10_000),
        tick_age_secs: 10,
        active_subscriptions: 12,
        subscribed_symbols: 12,
      },
    ),
    vcgScanTime: iso(nowMs - 5 * 60_000),
    gexScanTime: iso(nowMs - 5 * 60_000),
    journalWrittenAt,
    journalSync: pipe.journalSync,
    journalGapSli: pipe.journalGapSli,
  };
}

describe("evaluateJournalCheck — pipeline liveness", () => {
  const QUIET_FILL_MS = 5 * 24 * 3_600_000;

  it("(d) is not applicable outside RTH", () => {
    const input = {
      writtenAt: iso(OPEN_NOW - QUIET_FILL_MS),
      ...pipelineRows(OPEN_NOW),
    };
    expect(evaluateJournalCheck(input, "closed", CLOSED_NOW)).toEqual(NOT_APPLICABLE);
    expect(evaluateJournalCheck(input, "extended", EXTENDED_NOW)).toEqual(NOT_APPLICABLE);
  });

  it("(a) no fills for 5+ days is fresh when sync and gap-sli are healthy", () => {
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(OPEN_NOW - QUIET_FILL_MS),
        ...pipelineRows(OPEN_NOW),
      },
      "open",
      OPEN_NOW,
    );
    expect(result).toEqual({
      applicable: true,
      age_secs: QUIET_FILL_MS / 1000,
      fresh: true,
    });
  });

  it("(b) a journal-sync heartbeat past its open window is stale", () => {
    const windowMs = getFreshnessWindowMs("journal-sync", "open");
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(OPEN_NOW - 3_600_000),
        ...pipelineRows(OPEN_NOW, { syncAt: iso(OPEN_NOW - windowMs - 1_000) }),
      },
      "open",
      OPEN_NOW,
    );
    expect(result.fresh).toBe(false);
    expect(result.applicable).toBe(true);
    expect(result.age_secs).toBe(3_600);
  });

  it("journal-sync still inside its open window stays fresh", () => {
    const windowMs = getFreshnessWindowMs("journal-sync", "open");
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(OPEN_NOW - QUIET_FILL_MS),
        ...pipelineRows(OPEN_NOW, { syncAt: iso(OPEN_NOW - windowMs + 1_000) }),
      },
      "open",
      OPEN_NOW,
    );
    expect(result.fresh).toBe(true);
  });

  it("one-cycle journal-sync error stays fresh", () => {
    // The error streak just started. updated_at is the error write; notOkSince
    // is that same transition. It was ok on the previous cycle.
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(OPEN_NOW - 3_600_000),
        ...pipelineRows(OPEN_NOW, {
          syncState: "error",
          syncAt: iso(OPEN_NOW - 30_000),
          notOkSince: iso(OPEN_NOW - 30_000),
        }),
      },
      "open",
      OPEN_NOW,
    );
    expect(result.fresh).toBe(true);
    expect(result.applicable).toBe(true);
  });

  it("journal-sync not ok for longer than its open window is stale", () => {
    const windowMs = getFreshnessWindowMs("journal-sync", "open");
    // Error heartbeats keep moving updated_at. The streak itself is past the window.
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(OPEN_NOW - 3_600_000),
        ...pipelineRows(OPEN_NOW, {
          syncState: "error",
          syncAt: iso(OPEN_NOW - 30_000),
          notOkSince: iso(OPEN_NOW - windowMs - 1_000),
        }),
      },
      "open",
      OPEN_NOW,
    );
    expect(result.fresh).toBe(false);
  });

  it("a journal-sync error with no streak timestamp is not fresh", () => {
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(OPEN_NOW - 3_600_000),
        ...pipelineRows(OPEN_NOW, { syncState: "error", notOkSince: null }),
      },
      "open",
      OPEN_NOW,
    );
    expect(result.fresh).toBe(false);
  });

  it("(c) gap-sli missing_exec_id_count>0 is stale while journal-sync is live", () => {
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(OPEN_NOW - 3_600_000),
        ...pipelineRows(OPEN_NOW, { missing: 2, gapState: "ok" }),
      },
      "open",
      OPEN_NOW,
    );
    expect(result.fresh).toBe(false);
    expect(result.age_secs).toBe(3_600);
  });

  it("a stale gap-sli row does not page while journal-sync is live", () => {
    const windowMs = getFreshnessWindowMs("journal-gap-sli", "open");
    const payload = buildFreshnessPayload(
      freshnessAt(
        OPEN_NOW,
        iso(OPEN_NOW - 3_600_000),
        pipelineRows(OPEN_NOW, { gapAt: iso(OPEN_NOW - windowMs - 1_000) }),
      ),
      new Date(OPEN_NOW),
    );
    expect(payload.checks.journal.fresh).toBe(true);
    expect(payload.all_fresh).toBe(true);
    expect(payload.journal_gap_warning).toBe(
      "journal-gap-sli heartbeat is stale; journal freshness follows journal-sync",
    );
  });

  it("gap-sli self-error with healthy sync is fresh and warns", () => {
    const payload = buildFreshnessPayload(
      freshnessAt(
        OPEN_NOW,
        iso(OPEN_NOW - 3_600_000),
        pipelineRows(OPEN_NOW, {
          gapState: "error",
          gapError: "Hrana: api error 404 stream not found",
        }),
      ),
      new Date(OPEN_NOW),
    );
    expect(payload.checks.journal.fresh).toBe(true);
    expect(payload.all_fresh).toBe(true);
    expect(payload.journal_gap_warning).toBe(
      "journal-gap-sli reported an error; journal freshness follows journal-sync",
    );
  });

  it("gap-sli self-error does not hide a stale journal-sync", () => {
    const windowMs = getFreshnessWindowMs("journal-sync", "open");
    const payload = buildFreshnessPayload(
      freshnessAt(
        OPEN_NOW,
        iso(OPEN_NOW - 3_600_000),
        pipelineRows(OPEN_NOW, {
          syncState: "error",
          syncAt: iso(OPEN_NOW - 30_000),
          notOkSince: iso(OPEN_NOW - windowMs - 1_000),
          gapState: "error",
          gapError: "Hrana: api error 404 stream not found",
        }),
      ),
      new Date(OPEN_NOW),
    );
    expect(payload.checks.journal.fresh).toBe(false);
    expect(payload.all_fresh).toBe(false);
    expect(payload.journal_gap_warning).toBe(
      "journal-gap-sli reported an error; journal freshness follows journal-sync",
    );
  });

  it("a missing pipeline row is not fresh during RTH", () => {
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(OPEN_NOW - 3_600_000),
        journalSync: null,
        journalGapSli: pipelineRows(OPEN_NOW).journalGapSli,
      },
      "open",
      OPEN_NOW,
    );
    expect(result).toEqual({ applicable: true, age_secs: 3_600, fresh: false });
    expect(
      evaluateJournalCheck(
        {
          writtenAt: iso(OPEN_NOW - 3_600_000),
          journalSync: pipelineRows(OPEN_NOW).journalSync,
          journalGapSli: null,
        },
        "open",
        OPEN_NOW,
      ).fresh,
    ).toBe(false);
  });

  it("an empty journal is fresh when the pipeline is live", () => {
    expect(
      evaluateJournalCheck(
        { writtenAt: null, ...pipelineRows(OPEN_NOW) },
        "open",
        OPEN_NOW,
      ),
    ).toEqual({ applicable: true, age_secs: null, fresh: true });
  });

  it("open-bell grace: a pre-open journal-sync row is live for the open window", () => {
    // Mon 2026-11-16 09:35 ET. Last sync write is Fri 16:00 ET, before today's open.
    const at = Date.parse("2026-11-16T14:35:00Z");
    const fridayClose = Date.parse("2026-11-13T21:00:00Z");
    const result = evaluateJournalCheck(
      {
        writtenAt: iso(at - QUIET_FILL_MS),
        ...pipelineRows(at, { syncAt: iso(fridayClose) }),
      },
      "open",
      at,
    );
    expect(result.fresh).toBe(true);

    const pastGrace = Date.parse("2026-11-16T14:45:00Z");
    expect(
      evaluateJournalCheck(
        {
          writtenAt: iso(pastGrace - QUIET_FILL_MS),
          ...pipelineRows(pastGrace, { syncAt: iso(fridayClose) }),
        },
        "open",
        pastGrace,
      ).fresh,
    ).toBe(false);
  });
});

describe("buildFreshnessPayload — quiet book", () => {
  const PRODUCTION_NOW = Date.parse("2026-11-17T15:00:00Z");
  const PRODUCTION_FILL = Date.parse("2026-11-13T21:07:21Z");

  it("(a) all_fresh is true in RTH when the only stale fact is last-fill age", () => {
    const payload = buildFreshnessPayload(
      freshnessAt(OPEN_NOW, iso(OPEN_NOW - 5 * 24 * 3_600_000)),
      new Date(OPEN_NOW),
    );
    expect(payload.market_state).toBe("open");
    expect(payload.checks.journal.fresh).toBe(true);
    expect(payload.checks.relay_tick.fresh).toBe(true);
    expect(payload.checks.vcg_scan.fresh).toBe(true);
    expect(payload.checks.gex_scan.fresh).toBe(true);
    expect(payload.all_fresh).toBe(true);
    expect(payload.journal_last_fill.age_secs).toBe(5 * 24 * 3_600);
    expect(payload.journal_last_fill.warning).toBe(
      "last fill is older than 3 days; journal freshness follows pipeline liveness",
    );
  });

  it("(b) all_fresh is false when journal-sync is stale", () => {
    const windowMs = getFreshnessWindowMs("journal-sync", "open");
    const payload = buildFreshnessPayload(
      freshnessAt(
        OPEN_NOW,
        iso(OPEN_NOW - 3_600_000),
        pipelineRows(OPEN_NOW, { syncAt: iso(OPEN_NOW - windowMs - 1_000) }),
      ),
      new Date(OPEN_NOW),
    );
    expect(payload.checks.journal.fresh).toBe(false);
    expect(payload.all_fresh).toBe(false);
    expect(payload.journal_last_fill.warning).toBeNull();
  });

  it("(c) all_fresh is false when gap-sli reports missing executions", () => {
    const payload = buildFreshnessPayload(
      freshnessAt(
        OPEN_NOW,
        iso(OPEN_NOW - 3_600_000),
        pipelineRows(OPEN_NOW, { missing: 1 }),
      ),
      new Date(OPEN_NOW),
    );
    expect(payload.checks.journal.fresh).toBe(false);
    expect(payload.all_fresh).toBe(false);
  });

  it("(d) outside RTH a quiet book does not page", () => {
    const quiet = freshnessAt(OPEN_NOW, iso(OPEN_NOW - 5 * 24 * 3_600_000));
    for (const now of [EXTENDED_NOW, CLOSED_NOW]) {
      const payload = buildFreshnessPayload(quiet, new Date(now));
      expect(payload.checks.journal).toEqual(NOT_APPLICABLE);
      expect(payload.all_fresh).toBeNull();
    }
  });

  it("production 2026-11-17 quiet book flips from stale to fresh", () => {
    const payload = buildFreshnessPayload(
      freshnessAt(PRODUCTION_NOW, new Date(PRODUCTION_FILL).toISOString()),
      new Date(PRODUCTION_NOW),
    );
    const age = payload.checks.journal.age_secs;
    expect(payload.market_state).toBe("open");
    expect(age).toBe(Math.round((PRODUCTION_NOW - PRODUCTION_FILL) / 1000));
    expect(age).toBe(323559);
    expect(age).toBeGreaterThan(3 * 24 * 3_600);
    expect(payload.checks.journal.fresh).toBe(true);
    expect(payload.all_fresh).toBe(true);
    expect(payload.journal_last_fill).toEqual({
      age_secs: age,
      warning: "last fill is older than 3 days; journal freshness follows pipeline liveness",
    });
  });
});

describe("buildFreshnessPayload", () => {
  const FRESH_INPUTS = {
    relayRow: relayRow(),
    vcgScanTime: iso(OPEN_NOW - 5 * 60_000),
    gexScanTime: iso(OPEN_NOW - 5 * 60_000),
    journalWrittenAt: iso(OPEN_NOW - 3_600_000),
    ...pipelineRows(OPEN_NOW),
  };

  it("matches the fixed contract shape", () => {
    const payload = buildFreshnessPayload(FRESH_INPUTS, new Date(OPEN_NOW));
    expect(Object.keys(payload).sort()).toEqual([
      "all_fresh",
      "checks",
      "database_failures",
      "database_ok",
      "generated_at",
      "journal_gap_warning",
      "journal_last_fill",
      "market_state",
    ]);
    expect(payload.journal_last_fill).toEqual({ age_secs: 3600, warning: null });
    expect(payload.journal_gap_warning).toBeNull();
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
