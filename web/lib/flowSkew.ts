/**
 * The flow report's skew block, as the operator reads it.
 *
 * Source: `scripts/vol_skew_mr_scanner.py:fetch_skew_snapshot`, the same
 * snapshot the Vol/Skew MR scanner gates on. Value is 25-delta put IV minus
 * call IV in vol points for one listed expiry nearest 30 DTE; `path` is the
 * scanner's direction over its latest six sessions.
 */

export type FlowSkewPath = "rising" | "falling" | "flat" | "unknown";

export type FlowSkewSession = { date: string; value: number };

export type FlowSkew = {
  expiry: string | null;
  delta: number;
  sessions: FlowSkewSession[];
  value: number | null;
  prior: number | null;
  change: number | null;
  path: FlowSkewPath;
  errors?: string[];
};

export type FlowSkewTone = "positive" | "negative" | "neutral";

export type FlowSkewView = {
  /** A value is on the report; false for pre-skew caches and failed reads. */
  available: boolean;
  valueLabel: string;
  unitLabel: string;
  pathLabel: string;
  tone: FlowSkewTone;
  changeLabel: string | null;
  expiryLabel: string | null;
  note: string;
};

const DAY_MS = 24 * 60 * 60 * 1000;

function signed(value: number): string {
  return `${value >= 0 ? "+" : "-"}${Math.abs(value).toFixed(2)}`;
}

function daysToExpiry(expiry: string, now: Date): number {
  const expiryMs = Date.parse(`${expiry}T00:00:00Z`);
  const todayMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.max(0, Math.round((expiryMs - todayMs) / DAY_MS));
}

function expiryLabel(expiry: string | null, now: Date): string | null {
  if (!expiry) return null;
  const parsed = new Date(`${expiry}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  const month = parsed.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  return `${month} ${parsed.getUTCDate()} · ${daysToExpiry(expiry, now)} DTE`;
}

function pathTone(path: FlowSkewPath): FlowSkewTone {
  if (path === "rising") return "negative";
  if (path === "falling") return "positive";
  return "neutral";
}

function pathNote(path: FlowSkewPath, value: number): string {
  if (path === "rising") {
    return value < 0
      ? "Puts richening against calls. The call premium over puts is narrowing."
      : "Puts richening against calls. Downside protection is being bid.";
  }
  if (path === "falling") {
    return value < 0
      ? "Puts cheapening against calls. The call premium over puts is widening."
      : "Puts cheapening against calls. Downside fear is easing.";
  }
  if (path === "flat") {
    return value < 0
      ? "Calls richer than puts and holding. Upside is where the demand sits."
      : "Put wing steady against calls over the last sessions.";
  }
  return "Fewer than two comparable sessions, so no direction yet.";
}

function unavailableNote(errors: string[]): string {
  if (errors.some((error) => error.includes("no_future_listed_expiry"))) {
    return "No listed expiry near 30 days to price skew from.";
  }
  return "Skew history unavailable from Unusual Whales on this scan.";
}

export function describeFlowSkew(skew: FlowSkew | undefined, now: Date = new Date()): FlowSkewView {
  if (!skew) {
    return {
      available: false,
      valueLabel: "--",
      unitLabel: "vol pts",
      pathLabel: "PENDING",
      tone: "neutral",
      changeLabel: null,
      expiryLabel: null,
      note: "Not on this report yet. Refresh to sample the current skew.",
    };
  }
  if (skew.value == null) {
    return {
      available: false,
      valueLabel: "--",
      unitLabel: "vol pts",
      pathLabel: "UNAVAILABLE",
      tone: "neutral",
      changeLabel: null,
      expiryLabel: expiryLabel(skew.expiry, now),
      note: unavailableNote(skew.errors ?? []),
    };
  }
  return {
    available: true,
    valueLabel: signed(skew.value),
    unitLabel: "vol pts",
    pathLabel: skew.path === "unknown" ? "NO PATH" : skew.path.toUpperCase(),
    tone: pathTone(skew.path),
    changeLabel: skew.change == null ? null : `${signed(skew.change)} vs prior session`,
    expiryLabel: expiryLabel(skew.expiry, now),
    note: pathNote(skew.path, skew.value),
  };
}
