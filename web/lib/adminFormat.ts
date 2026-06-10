/**
 * Pure helpers for the operator admin panel. Kept dependency-free so the
 * component logic is unit-testable without a DOM.
 */

import type {
  IbAuthState,
  PushLockState,
  RestartBackoffState,
  ServiceAction,
  UnitKind,
  UnitStatus,
  UnitVerdict,
} from "./adminTypes";

export type AuthStateTone = "positive" | "warning" | "negative" | "neutral";

/**
 * Map an auth_state to the brand tone token used by the status pill. Keeps
 * the colour decision in one place so the same mapping applies to mobile
 * banners, future status badges, etc.
 */
export function authStateTone(state: IbAuthState | undefined | null): AuthStateTone {
  switch (state) {
    case "authenticated":
      return "positive";
    case "awaiting_2fa":
      return "warning";
    case "unreachable":
      return "negative";
    case "remote":
      return "neutral";
    case "unknown":
    default:
      return "neutral";
  }
}

/** Title-case label for an auth_state, e.g. "awaiting_2fa" -> "Awaiting 2FA". */
export function authStateLabel(state: IbAuthState | undefined | null): string {
  switch (state) {
    case "authenticated":
      return "Authenticated";
    case "awaiting_2fa":
      return "Awaiting 2FA";
    case "unreachable":
      return "Unreachable";
    case "remote":
      return "Remote";
    case "unknown":
      return "Unknown";
    default:
      return "Unknown";
  }
}

/**
 * Should the "Force 2FA Push" button be disabled? True when the cross-process
 * push lock is held (a restart is in flight) OR a network call is pending.
 */
export function isForcePushDisabled(opts: {
  pushLock: PushLockState | null | undefined;
  pending: boolean;
}): boolean {
  if (opts.pending) return true;
  if (opts.pushLock && opts.pushLock.remaining_secs > 0) return true;
  return false;
}

/** Human-readable reason for the disabled state, for tooltips. */
export function forcePushDisabledReason(opts: {
  pushLock: PushLockState | null | undefined;
  pending: boolean;
}): string | null {
  if (opts.pending) return "Restart in flight";
  if (opts.pushLock && opts.pushLock.remaining_secs > 0) {
    return `Another restart is in flight (held by ${opts.pushLock.holder} for ${opts.pushLock.remaining_secs}s)`;
  }
  return null;
}

/** Brief backoff summary, e.g. "3 attempts, next in 120s". */
export function backoffSummary(backoff: RestartBackoffState | null | undefined): string {
  if (!backoff || backoff.attempt_count === 0) {
    return "No backoff active";
  }
  return `${backoff.attempt_count} attempt${backoff.attempt_count === 1 ? "" : "s"}, next in ${backoff.next_attempt_in_secs}s`;
}

/**
 * Coarse tone for a systemd unit so the row can render a colored dot.
 * The mapping is intentionally narrow: anything non-active is treated as
 * a problem so an operator can spot bad rows immediately.
 */
export function unitTone(unit: UnitStatus): "positive" | "warning" | "negative" | "neutral" {
  if (!unit.can_control) return "neutral";
  if (unit.active_state === "active" && unit.sub_state === "running") return "positive";
  if (unit.active_state === "activating" || unit.active_state === "reloading") return "warning";
  if (unit.active_state === "failed") return "negative";
  // ``inactive (dead)`` is normal for ``Type=oneshot`` services that have
  // finished cleanly. Treat them as positive when the last exit code is 0;
  // anything else is a warning (might be a stuck or failed run).
  if (unit.active_state === "inactive") {
    if (typeof unit.last_exit_code === "number") {
      return unit.last_exit_code === 0 ? "positive" : "negative";
    }
    return "warning";
  }
  return "neutral";
}

/**
 * "running 3h 22m" for a daemon, "last ran 5m ago (rc=0)" for a oneshot,
 * "never run" when both timestamps are missing.
 *
 * Pulled out of the component so we can unit-test the wording without
 * mounting the table.
 */
export function unitActivityLabel(unit: UnitStatus, now: number = Date.now()): string {
  if (typeof unit.uptime_secs === "number" && unit.uptime_secs >= 0) {
    return `running ${formatUptime(unit.uptime_secs)}`;
  }
  if (unit.last_active_at) {
    const relative = formatRelativeTime(unit.last_active_at, now);
    if (typeof unit.last_exit_code === "number") {
      return `last ran ${relative} (rc=${unit.last_exit_code})`;
    }
    return `last ran ${relative}`;
  }
  return "never run";
}

const SECOND_MS = 1_000;
const MINUTE_MS = 60 * SECOND_MS;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * Compact relative-time string for the operator panel: "just now", "23s
 * ago", "5m ago", "3h ago", "yesterday", "3d ago". Always backward-looking
 * — future timestamps clamp to "just now" (clock skew safeguard).
 */
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "unknown";
  const delta = now - t;
  if (delta < 5 * SECOND_MS) return "just now";
  if (delta < MINUTE_MS) return `${Math.floor(delta / SECOND_MS)}s ago`;
  if (delta < HOUR_MS) return `${Math.floor(delta / MINUTE_MS)}m ago`;
  if (delta < DAY_MS) return `${Math.floor(delta / HOUR_MS)}h ago`;
  if (delta < 2 * DAY_MS) return "yesterday";
  return `${Math.floor(delta / DAY_MS)}d ago`;
}

/**
 * Compact uptime for a long-running daemon: "45s", "3m", "3h 22m", "2d 4h",
 * "12d". Designed to be glanceable so the row stays readable.
 */
export function formatUptime(secs: number): string {
  if (!Number.isFinite(secs) || secs < 0) return "0s";
  if (secs < 60) return `${Math.floor(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86_400) {
    const hours = Math.floor(secs / 3600);
    const minutes = Math.floor((secs % 3600) / 60);
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  const days = Math.floor(secs / 86_400);
  const hours = Math.floor((secs % 86_400) / 3600);
  return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
}

// ---------------------------------------------------------------------------
// Service verdict + kind (redesign): turn raw systemd active/sub state into a
// single glanceable verdict so the operator never has to mentally translate
// "inactive (dead) rc=0" into "this is fine".
// ---------------------------------------------------------------------------

/**
 * The long-lived daemons of the radon-* stack. A STOPPED daemon reports no
 * `uptime_secs`, so it can't be told from a never-run oneshot by uptime alone
 * (that misreads an outage as "idle") — anchor on the documented set, and fall
 * back to the uptime/oneshot heuristic for anything not enumerated.
 */
const DAEMON_UNITS: ReadonlySet<string> = new Set([
  "radon-api.service",
  "radon-relay.service",
  "radon-monitor.service",
  "radon-nextjs.service",
  "radon-ib-gateway.service",
  "radon-health.service",
  "radon-newsfeed.service",
]);

/**
 * Daemons run continuously; scheduled jobs are oneshots that settle at
 * `inactive (dead)` and report a `last_exit_code`. For a daemon `inactive` is an
 * outage; for a job it is normal.
 */
export function unitKind(unit: UnitStatus): UnitKind {
  if (DAEMON_UNITS.has(unit.unit)) return "daemon";
  if (typeof unit.uptime_secs === "number") return "daemon";
  return "job";
}

/**
 * Static cascade map: stopping a key carries down its dependents, which do NOT
 * auto-restart (feedback_systemd_cascade_stop_no_autorecover). Builds the
 * high-severity Stop dialog. The cascade is fixed + documented, so no API call.
 */
export const UNIT_DEPENDENTS: Record<string, string[]> = {
  "radon-ib-gateway.service": [
    "radon-api.service",
    "radon-relay.service",
    "radon-monitor.service",
  ],
};

/** Dependents that would also stop, for a unit's Stop confirmation. */
export function unitDependents(unit: string): string[] {
  return UNIT_DEPENDENTS[unit] ?? [];
}

/**
 * One-word verdict + brand tone for a unit. Puts unitTone()'s oneshot-rc logic
 * on screen as a label instead of leaving it implicit in the dot.
 */
export function unitVerdict(unit: UnitStatus): UnitVerdict {
  if (!unit.can_control) return { label: "Unknown", tone: "neutral" };
  const active = unit.active_state;
  const sub = unit.sub_state;
  if (active === "activating" || active === "reloading") return { label: "Starting", tone: "warning" };
  if (active === "deactivating") return { label: "Stopping", tone: "warning" };
  if (active === "failed") return { label: "Failed", tone: "negative" };
  if (active === "active") {
    // active+running is a live daemon; active+exited is a oneshot that ran clean.
    if (sub === "running" || unitKind(unit) === "daemon") return { label: "Running", tone: "positive" };
    return { label: "Idle", tone: "positive" };
  }
  if (active === "inactive") {
    if (unitKind(unit) === "job") {
      if (unit.last_exit_code === 0) return { label: "Idle", tone: "positive" };
      if (typeof unit.last_exit_code === "number") return { label: "Failed", tone: "negative" };
      return { label: "Idle", tone: "neutral" }; // never run
    }
    return { label: "Stopped", tone: "negative" }; // a daemon being down IS an outage
  }
  return { label: "Unknown", tone: "neutral" };
}

// ---------------------------------------------------------------------------
// Gateway power state: the dedicated Stop/Start control inside the IB Gateway
// controls card. Drive the button primarily off the gateway unit's
// active_state (the authoritative systemd verdict), with port_listening as a
// secondary hint — port_listening can linger true for a moment after a stop
// (socket lingering) or read true while awaiting_2fa (port up, not authed).
// ---------------------------------------------------------------------------

export type GatewayPowerState = "running" | "stopped" | "transitional";

/**
 * Derive the power state of the IB Gateway from the gateway systemd unit and
 * the /health port_listening flag. `activating`/`deactivating` are
 * transitional (button disabled so the operator can't double-fire). Otherwise
 * `active` (or a listening port as a fallback) is running; everything else
 * (inactive / failed / unknown, port down) is stopped.
 */
export function gatewayPowerState(opts: {
  unit: UnitStatus | null | undefined;
  portListening: boolean | null | undefined;
}): GatewayPowerState {
  const active = opts.unit?.active_state;
  if (active === "activating" || active === "reloading") return "transitional";
  if (active === "deactivating") return "transitional";
  if (active === "active") return "running";
  if (active === "inactive" || active === "failed") return "stopped";
  // No unit row (or an unknown state): fall back to the port hint.
  return opts.portListening ? "running" : "stopped";
}

/**
 * Why a service control button is disabled, for a self-explaining tooltip
 * (mirrors forcePushDisabledReason). Returns null when the button is enabled.
 */
export function serviceControlDisabledReason(opts: {
  unit: UnitStatus;
  action: ServiceAction;
  supported: boolean;
  pending: boolean;
}): string | null {
  if (!opts.supported) return "Read-only: this browser is not on the Hetzner VPS.";
  if (!opts.unit.can_control) return "This unit is not in the controllable allowlist.";
  if (opts.pending) return "Action in flight...";
  if (opts.action === "start" && unitVerdict(opts.unit).label === "Running") return "Already running.";
  return null;
}

// ---------------------------------------------------------------------------
// Detail humanization: some service_health writers persist structured JSON in
// their detail/last_error field (e.g. replica-watchdog heartbeat blobs). The UI
// must NEVER render a raw JSON string — turn it into a readable "key: value"
// summary, and pass genuine human text through unchanged.
// ---------------------------------------------------------------------------

function humanizeKey(key: string): string {
  return key.replace(/_/g, " ").trim();
}

function humanizeValue(value: unknown): string {
  if (value === null) return "none";
  if (typeof value === "boolean") return value ? "yes" : "no";
  // ISO-8601 datetime -> deterministic UTC HH:MM:SS (avoids locale/TZ flakiness).
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(value)) {
    return `${value.slice(11, 19)} UTC`;
  }
  if (Array.isArray(value)) return value.map(humanizeValue).join(", ");
  if (typeof value === "object") return humanizeJsonObject(value as Record<string, unknown>);
  return String(value);
}

function humanizeJsonObject(obj: Record<string, unknown>): string {
  return Object.entries(obj)
    .map(([key, value]) => `${humanizeKey(key)}: ${humanizeValue(value)}`)
    .join(" · "); // " · "
}

/**
 * Render a service_health detail/last_error value as human-readable text.
 * Empty -> "" (caller shows a placeholder). A JSON object/array -> a compact
 * "key: value · key: value" summary. Anything else -> the trimmed string.
 * Guarantees a raw JSON blob never reaches the UI.
 */
export function humanizeDetail(raw: string | null | undefined): string {
  if (!raw) return "";
  const text = raw.trim();
  if (!text) return "";
  if (text.startsWith("{") || text.startsWith("[")) {
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object") return humanizeValue(parsed);
    } catch {
      // Not valid JSON despite the leading brace — fall through to raw text.
    }
  }
  return text;
}
