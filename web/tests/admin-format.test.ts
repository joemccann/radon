/**
 * Unit tests for the pure helpers in ``lib/adminFormat.ts``. These are the
 * source of truth for status pill colours, force-push gating, and per-unit
 * tones — keeping them in pure functions means the UI components stay
 * trivially testable in jsdom (next file over).
 */
import { describe, expect, it } from "vitest";
import {
  authStateLabel,
  authStateTone,
  backoffSummary,
  formatRelativeTime,
  formatUptime,
  forcePushDisabledReason,
  gatewayPowerState,
  isForcePushDisabled,
  unitActivityLabel,
  unitTone,
} from "../lib/adminFormat";
import type { UnitStatus } from "../lib/adminTypes";

function gatewayRow(active_state: string, sub_state = "running"): UnitStatus {
  return {
    unit: "radon-ib-gateway.service",
    load_state: "loaded",
    active_state,
    sub_state,
    description: "IB Gateway container",
    can_control: true,
  };
}

describe("gatewayPowerState", () => {
  it("reports running when the unit is active", () => {
    expect(gatewayPowerState({ unit: gatewayRow("active"), portListening: true })).toBe("running");
  });
  it("reports stopped when the unit is inactive even if the port still lingers", () => {
    expect(gatewayPowerState({ unit: gatewayRow("inactive", "dead"), portListening: true })).toBe(
      "stopped",
    );
  });
  it("reports stopped when the unit failed", () => {
    expect(gatewayPowerState({ unit: gatewayRow("failed", "failed"), portListening: false })).toBe(
      "stopped",
    );
  });
  it("reports transitional while activating or deactivating", () => {
    expect(gatewayPowerState({ unit: gatewayRow("activating", "start"), portListening: false })).toBe(
      "transitional",
    );
    expect(gatewayPowerState({ unit: gatewayRow("deactivating", "stop"), portListening: true })).toBe(
      "transitional",
    );
  });
  it("falls back to the port hint when there is no unit row", () => {
    expect(gatewayPowerState({ unit: null, portListening: true })).toBe("running");
    expect(gatewayPowerState({ unit: null, portListening: false })).toBe("stopped");
  });
});

describe("authStateTone", () => {
  it("maps authenticated to positive", () => {
    expect(authStateTone("authenticated")).toBe("positive");
  });
  it("maps awaiting_2fa to warning", () => {
    expect(authStateTone("awaiting_2fa")).toBe("warning");
  });
  it("maps unreachable to negative", () => {
    expect(authStateTone("unreachable")).toBe("negative");
  });
  it("treats remote / unknown / null as neutral", () => {
    expect(authStateTone("remote")).toBe("neutral");
    expect(authStateTone("unknown")).toBe("neutral");
    expect(authStateTone(null)).toBe("neutral");
    expect(authStateTone(undefined)).toBe("neutral");
  });
});

describe("authStateLabel", () => {
  it("produces human copy for every documented state", () => {
    expect(authStateLabel("authenticated")).toBe("Authenticated");
    expect(authStateLabel("awaiting_2fa")).toBe("Awaiting 2FA");
    expect(authStateLabel("unreachable")).toBe("Unreachable");
    expect(authStateLabel("remote")).toBe("Remote");
    expect(authStateLabel("unknown")).toBe("Unknown");
    expect(authStateLabel(null)).toBe("Unknown");
  });
});

describe("isForcePushDisabled", () => {
  it("disables when pending", () => {
    expect(isForcePushDisabled({ pushLock: null, pending: true })).toBe(true);
  });
  it("disables when push lock is held with positive remaining", () => {
    expect(
      isForcePushDisabled({
        pushLock: {
          holder: "scripts.api.ib_gateway.restart_ib_gateway",
          acquired_at: 1000,
          expires_at: 2000,
          remaining_secs: 45,
          reason: "restart_ib_gateway",
        },
        pending: false,
      }),
    ).toBe(true);
  });
  it("does NOT disable when push lock object has zero remaining (expired)", () => {
    expect(
      isForcePushDisabled({
        pushLock: {
          holder: "ib_watchdog",
          acquired_at: 1000,
          expires_at: 1001,
          remaining_secs: 0,
          reason: null,
        },
        pending: false,
      }),
    ).toBe(false);
  });
  it("does NOT disable when no lock and not pending", () => {
    expect(isForcePushDisabled({ pushLock: null, pending: false })).toBe(false);
  });
});

describe("forcePushDisabledReason", () => {
  it("returns null when neither lock nor pending", () => {
    expect(forcePushDisabledReason({ pushLock: null, pending: false })).toBeNull();
  });
  it("returns 'Restart in flight' when pending", () => {
    expect(forcePushDisabledReason({ pushLock: null, pending: true })).toBe("Restart in flight");
  });
  it("returns lock holder + remaining when held", () => {
    const reason = forcePushDisabledReason({
      pushLock: {
        holder: "ib_watchdog",
        acquired_at: 0,
        expires_at: 0,
        remaining_secs: 30,
        reason: "watchdog",
      },
      pending: false,
    });
    expect(reason).toContain("ib_watchdog");
    expect(reason).toContain("30s");
  });
});

describe("backoffSummary", () => {
  it("reports no backoff when attempt_count is 0 or missing", () => {
    expect(backoffSummary(null)).toBe("No backoff active");
    expect(backoffSummary(undefined)).toBe("No backoff active");
    expect(
      backoffSummary({
        attempt_count: 0,
        last_attempt_at: 0,
        next_attempt_after: 0,
        next_attempt_in_secs: 0,
        last_outcome: null,
        push_lock: null,
      }),
    ).toBe("No backoff active");
  });
  it("singularises attempt count of 1", () => {
    expect(
      backoffSummary({
        attempt_count: 1,
        last_attempt_at: 0,
        next_attempt_after: 0,
        next_attempt_in_secs: 60,
        last_outcome: "awaiting_2fa",
        push_lock: null,
      }),
    ).toBe("1 attempt, next in 60s");
  });
  it("pluralises when count > 1", () => {
    expect(
      backoffSummary({
        attempt_count: 3,
        last_attempt_at: 0,
        next_attempt_after: 0,
        next_attempt_in_secs: 900,
        last_outcome: "awaiting_2fa",
        push_lock: null,
      }),
    ).toBe("3 attempts, next in 900s");
  });
});

describe("unitTone", () => {
  const base = {
    unit: "radon-api.service",
    load_state: "loaded",
    description: "Radon API",
    can_control: true,
  };
  it("active + running -> positive", () => {
    expect(unitTone({ ...base, active_state: "active", sub_state: "running" })).toBe("positive");
  });
  it("active + exited -> neutral fallback (not running)", () => {
    expect(unitTone({ ...base, active_state: "active", sub_state: "exited" })).toBe("neutral");
  });
  it("failed -> negative", () => {
    expect(unitTone({ ...base, active_state: "failed", sub_state: "failed" })).toBe("negative");
  });
  it("inactive without exit-code metadata -> warning", () => {
    expect(unitTone({ ...base, active_state: "inactive", sub_state: "dead" })).toBe("warning");
  });
  it("inactive oneshot with rc=0 -> positive (clean finish)", () => {
    expect(
      unitTone({
        ...base,
        active_state: "inactive",
        sub_state: "dead",
        last_exit_code: 0,
      }),
    ).toBe("positive");
  });
  it("inactive oneshot with non-zero rc -> negative", () => {
    expect(
      unitTone({
        ...base,
        active_state: "inactive",
        sub_state: "dead",
        last_exit_code: 1,
      }),
    ).toBe("negative");
  });
  it("activating -> warning", () => {
    expect(unitTone({ ...base, active_state: "activating", sub_state: "start" })).toBe("warning");
  });
  it("uncontrollable -> neutral regardless of state", () => {
    expect(
      unitTone({ ...base, can_control: false, active_state: "active", sub_state: "running" }),
    ).toBe("neutral");
  });
});

describe("formatRelativeTime", () => {
  const now = Date.parse("2026-05-19T12:00:00Z");
  it("returns 'just now' under 5s", () => {
    expect(formatRelativeTime("2026-05-19T11:59:58Z", now)).toBe("just now");
  });
  it("returns Ns ago for 5-59s", () => {
    expect(formatRelativeTime("2026-05-19T11:59:30Z", now)).toBe("30s ago");
  });
  it("returns Nm ago for 1-59 minutes", () => {
    expect(formatRelativeTime("2026-05-19T11:55:00Z", now)).toBe("5m ago");
  });
  it("returns Nh ago for 1-23 hours", () => {
    expect(formatRelativeTime("2026-05-19T09:00:00Z", now)).toBe("3h ago");
  });
  it("returns 'yesterday' between 24h and 48h", () => {
    expect(formatRelativeTime("2026-05-18T10:00:00Z", now)).toBe("yesterday");
  });
  it("returns Nd ago beyond 48h", () => {
    expect(formatRelativeTime("2026-05-16T12:00:00Z", now)).toBe("3d ago");
  });
  it("clamps future timestamps to 'just now' (clock skew safeguard)", () => {
    expect(formatRelativeTime("2026-05-19T12:01:00Z", now)).toBe("just now");
  });
  it("returns 'unknown' for non-parseable input", () => {
    expect(formatRelativeTime("not-a-date", now)).toBe("unknown");
  });
});

describe("formatUptime", () => {
  it("returns Ns for sub-minute", () => {
    expect(formatUptime(45)).toBe("45s");
  });
  it("returns Nm for sub-hour", () => {
    expect(formatUptime(125)).toBe("2m");
  });
  it("returns Nh when minutes are zero", () => {
    expect(formatUptime(3 * 3600)).toBe("3h");
  });
  it("returns Nh Nm for hours + minutes", () => {
    expect(formatUptime(3 * 3600 + 22 * 60)).toBe("3h 22m");
  });
  it("returns Nd Nh for days + hours", () => {
    expect(formatUptime(2 * 86_400 + 4 * 3600)).toBe("2d 4h");
  });
  it("returns Nd when hours are zero", () => {
    expect(formatUptime(12 * 86_400)).toBe("12d");
  });
  it("clamps negatives + non-finite values to 0s", () => {
    expect(formatUptime(-1)).toBe("0s");
    expect(formatUptime(Number.NaN)).toBe("0s");
  });
});

describe("unitActivityLabel", () => {
  const baseUnit = {
    unit: "radon-api.service",
    load_state: "loaded",
    active_state: "active",
    sub_state: "running",
    description: "Radon API",
    can_control: true,
  };
  const now = Date.parse("2026-05-19T12:00:00Z");
  it("prefers uptime for currently-running daemons", () => {
    expect(unitActivityLabel({ ...baseUnit, uptime_secs: 3 * 3600 + 22 * 60 }, now))
      .toBe("running 3h 22m");
  });
  it("falls back to last-ran timestamp for oneshots", () => {
    expect(
      unitActivityLabel(
        {
          ...baseUnit,
          active_state: "inactive",
          sub_state: "dead",
          last_active_at: "2026-05-19T11:55:00Z",
          last_exit_code: 0,
        },
        now,
      ),
    ).toBe("last ran 5m ago (rc=0)");
  });
  it("omits rc when no exit code is available (daemon that stopped)", () => {
    expect(
      unitActivityLabel(
        {
          ...baseUnit,
          active_state: "inactive",
          sub_state: "dead",
          last_active_at: "2026-05-19T11:55:00Z",
        },
        now,
      ),
    ).toBe("last ran 5m ago");
  });
  it("returns 'never run' when both timestamps are missing", () => {
    expect(unitActivityLabel(baseUnit, now)).toBe("never run");
  });
});
