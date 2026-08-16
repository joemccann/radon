/**
 * R6 / R7 / duplicate-LEGACY_PAYLOAD — the READ path decides staleness.
 *
 * R6 [P1] web/lib/performanceData.ts:113-115 (`resolveSessionsBehind`) prefers
 * the value the WRITER froze into the payload, and :86-89 / :729 drive `isStale`
 * off the payload's declared status word. A snapshot written on 2026-03-27 that
 * says `status: "ok"` and `nav_sessions_behind: 0` therefore still publishes a
 * confident +5.09% hero when it is read on 2026-08-15, 100 completed sessions
 * later. Deleting the declared field derives 100 correctly and it is STILL
 * ignored, because only `status` feeds `isStale`.
 *
 * DECISION 5: `resolveSessionsBehind` always derives from nav_as_of and returns
 * `max(declared, derived)`; `isStale` is driven by that derived number against
 * NAV_STALENESS_BUDGET_SESSIONS, independent of the declared status word.
 *
 * DECISION 6: sessions_behind counts trading sessions STRICTLY AFTER nav_as_of
 * up to and INCLUDING the last COMPLETED session (16:00 ET boundary). Two
 * tables pin it. The hand-written August one below drives the clock either side
 * of the 16:00 boundary, which the shared file cannot express. The SHARED file
 * — `tests/fixtures/perf/sessions_behind_parity.json`, the same bytes
 * `tests/test_perf_twr_residuals.py` parametrizes over — is what actually makes
 * this a parity pin: it carries MLK, Independence-observed, Thanksgiving,
 * Labor Day and the 101-session live stale-cache gap, all of which the August
 * window is silent about.
 */
import { readFileSync } from "fs";
import { resolve } from "path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildPerformanceView, normalizePerformanceData } from "../lib/performanceData";
import { TWR_GATES } from "../lib/performanceTwr";
import {
  goldenOkPayload,
  legacyV1LivePayload,
  type PerformancePayloadV2,
} from "./fixtures/performanceScenarios";

function viewOf(raw: unknown) {
  const view = buildPerformanceView(normalizePerformanceData(raw));
  if (!view) throw new Error("buildPerformanceView returned null");
  return view;
}

function withFrozenClock<T>(instant: string, run: () => T): T {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(instant));
  try {
    return run();
  } finally {
    vi.useRealTimers();
  }
}

// ---------------------------------------------------------------------------
// DECISION 6 — the shared sessions_behind case table
// ---------------------------------------------------------------------------

/**
 * 2026-08-10 Mon · 08-11 Tue · 08-12 Wed · 08-13 Thu · 08-14 Fri
 * 2026-08-15 Sat · 08-16 Sun · 08-17 Mon · 08-18 Tue
 * EDT is UTC-4 in August, so 14:00 ET = 18:00Z and 16:30 ET = 20:30Z.
 *
 * Each expectation is "weekdays strictly after nav_as_of, up to and including
 * the last COMPLETED session", counted by hand:
 */
const SESSIONS_BEHIND_CASES: Array<[navAsOf: string, nowUtc: string, expected: number, why: string]> = [
  // Fri 14:00 ET, pre-close -> last completed session is Thu 08-13.
  ["2026-08-13", "2026-08-14T18:00:00Z", 0, "nav IS the last completed session"],
  ["2026-08-12", "2026-08-14T18:00:00Z", 1, "Thu 08-13"],
  ["2026-08-11", "2026-08-14T18:00:00Z", 2, "Wed 08-12, Thu 08-13"],
  // Fri 16:30 ET, post-close -> Friday itself is now complete.
  ["2026-08-11", "2026-08-14T20:30:00Z", 3, "Wed 08-12, Thu 08-13, Fri 08-14"],
  ["2026-08-14", "2026-08-14T20:30:00Z", 0, "same day, post-close"],
  // Weekend -> walk back to Fri 08-14.
  ["2026-08-14", "2026-08-15T14:55:00Z", 0, "Saturday resolves to Fri 08-14"],
  // Mon 10:00 ET, pre-close -> last completed session is still Fri 08-14.
  ["2026-08-14", "2026-08-17T14:00:00Z", 0, "Monday pre-close"],
  // Tue 10:00 ET, pre-close -> last completed session is Mon 08-17.
  ["2026-08-14", "2026-08-18T14:00:00Z", 1, "Mon 08-17"],
  // A NAV dated after the last completed session is never "behind".
  ["2026-08-20", "2026-08-14T18:00:00Z", 0, "nav dated in the future"],
];

describe("sessions_behind — TS half of the Python parity table (R7 / DECISION 6)", () => {
  for (const [navAsOf, nowUtc, expected, why] of SESSIONS_BEHIND_CASES) {
    it(`nav_as_of ${navAsOf} read at ${nowUtc} => ${expected} (${why})`, () => {
      // No declared nav_sessions_behind: the read path must derive it.
      const payload = {
        ...goldenOkPayload(),
        nav_as_of: navAsOf,
        period_end: navAsOf,
      } as Partial<PerformancePayloadV2>;
      delete (payload as Record<string, unknown>).nav_sessions_behind;

      const behind = withFrozenClock(nowUtc, () => viewOf(payload).navSessionsBehind);
      expect(behind).toBe(expected);
    });
  }
});

// ---------------------------------------------------------------------------
// DECISION 6 — the SHARED parity table, byte-for-byte the one Python reads
// ---------------------------------------------------------------------------

type ParityCase = {
  nav_as_of: string;
  last_completed_session: string;
  expected: number;
  why: string;
};

const PARITY_TABLE = resolve(__dirname, "../../tests/fixtures/perf/sessions_behind_parity.json");
const PARITY_CASES: ParityCase[] = JSON.parse(readFileSync(PARITY_TABLE, "utf-8")).cases;

/** The table supplies the SESSION, not the instant; 20:45 ET on that session is
 *  the hour the radon-perf-twr timer fires, well past the 16:00 close, so it
 *  resolves to exactly the `last_completed_session` the Python side is handed. */
function timerHourOn(session: string): string {
  return `${session}T20:45:00-04:00`;
}

describe("sessions_behind — the shared Python/TS parity table (R7 / DECISION 6)", () => {
  it("reads a non-trivial table that actually contains holidays", () => {
    expect(PARITY_CASES.length).toBeGreaterThanOrEqual(10);
    // If someone strips the holiday rows, this stops being a parity pin.
    expect(PARITY_CASES.some((c) => c.expected === 101)).toBe(true);
  });

  for (const parityCase of PARITY_CASES) {
    it(`${parityCase.nav_as_of} -> ${parityCase.last_completed_session} = ${parityCase.expected}`, () => {
      const payload = {
        ...goldenOkPayload(),
        nav_as_of: parityCase.nav_as_of,
        period_end: parityCase.nav_as_of,
      } as Partial<PerformancePayloadV2>;
      delete (payload as Record<string, unknown>).nav_sessions_behind;

      const behind = withFrozenClock(
        timerHourOn(parityCase.last_completed_session),
        () => viewOf(payload).navSessionsBehind,
      );
      expect(behind, parityCase.why).toBe(parityCase.expected);
    });
  }
});

// ---------------------------------------------------------------------------
// R6 / DECISION 5 — staleness is enforced at READ time
// ---------------------------------------------------------------------------

/**
 * goldenOkPayload() is stamped generated_at 2026-03-27, nav_as_of 2026-03-27
 * (a Friday), status "ok", nav_sessions_behind 0.
 *
 * Read on Saturday 2026-08-15 the last completed session is Fri 2026-08-14, and
 * the SESSIONS strictly after 2026-03-27 through 2026-08-14 number 96:
 *   weekdays  Mar 30-31 (2) + Apr (22) + May (21) + Jun (22) + Jul (23)
 *             + Aug 3-14 (10) = 100
 *   less the four full-closure holidays inside that window: 2026-04-03 (Good
 *   Friday), 2026-05-25 (Memorial), 2026-06-19 (Juneteenth), 2026-07-03
 *   (Independence observed) = 100 - 4 = 96
 * Python agrees to the integer: `sessions_behind("2026-03-27", date(2026,8,14))
 * == 96`. 96 > NAV_STALENESS_BUDGET_SESSIONS (2), so the payload is stale on
 * arrival no matter what word it printed in March.
 */
const READ_INSTANT = "2026-08-15T14:55:40Z";
const DERIVED_SESSIONS_BEHIND = 96;

describe("a frozen v2 payload cannot publish confidently months later (R6)", () => {
  const variants: Array<[string, () => PerformancePayloadV2]> = [
    ["nav_sessions_behind declared 0", () => goldenOkPayload()],
    [
      "nav_sessions_behind absent entirely",
      () => {
        const payload = goldenOkPayload();
        delete (payload as unknown as Record<string, unknown>).nav_sessions_behind;
        return payload;
      },
    ],
  ];

  for (const [label, build] of variants) {
    describe(label, () => {
      it("derives 96 sessions behind, never the writer's frozen number", () => {
        const view = withFrozenClock(READ_INSTANT, () => viewOf(build()));
        expect(view.navSessionsBehind).toBe(DERIVED_SESSIONS_BEHIND);
      });

      it("is stale, even though the payload declares status ok", () => {
        const view = withFrozenClock(READ_INSTANT, () => viewOf(build()));
        expect(view.navSessionsBehind).toBeGreaterThan(TWR_GATES.NAV_STALENESS_BUDGET_SESSIONS);
        expect(view.isStale).toBe(true);
      });

      it("publishes no confident TWR: the reader decides, not the payload", () => {
        // Live hero today: "+5.09%" with no banner, from cum_return
        // 0.05092267338835921 written five months earlier.
        const view = withFrozenClock(READ_INSTANT, () => viewOf(build()));
        expect(view.twrCumReturn).toBeNull();
      });
    });
  }

  it("still reads as fresh on the day it was written", () => {
    // Anti-pin for the fix: 2026-03-27 16:30 ET is post-close on the NAV's own
    // session, so 0 sessions behind and nothing is suppressed.
    const view = withFrozenClock("2026-03-27T20:30:00Z", () => viewOf(goldenOkPayload()));
    expect(view.navSessionsBehind).toBe(0);
    expect(view.isStale).toBe(false);
    expect(view.twrCumReturn).toBe(0.05092267338835921);
  });
});

// ---------------------------------------------------------------------------
// Minor — LEGACY_PAYLOAD is prepended twice
// ---------------------------------------------------------------------------

describe("legacy warning is stated once (performanceData.ts:446 and :722)", () => {
  it("does not prepend LEGACY_PAYLOAD in normalizeV1 and again in buildPerformanceView", () => {
    const view = withFrozenClock(READ_INSTANT, () => viewOf(legacyV1LivePayload()));
    const legacyWarnings = view.warnings.filter((w) => w.code === "LEGACY_PAYLOAD");
    expect(legacyWarnings).toHaveLength(1);
  });

  it("shows the operator one flag per problem, not one per normalizer", () => {
    const view = withFrozenClock(READ_INSTANT, () => viewOf(legacyV1LivePayload()));
    const codes = view.warnings.map((w) => w.code);
    expect(new Set(codes).size).toBe(codes.length);
  });
});
