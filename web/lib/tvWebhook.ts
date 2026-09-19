// TradingView alert webhook — auth, size cap, and body parsing.
// Spec: docs/tradingview-integration.md (Phase 1). Transport lives in
// app/api/webhooks/tradingview/[token]/route.ts.
//
// TradingView signs nothing and never retries, so the only secrets are the
// path token (TV_WEBHOOK_PATH_TOKEN) and a `secret` field in the body
// (TV_WEBHOOK_SECRET). Each env var accepts a comma-separated pair so old and
// new values both pass during a rotation overlap.

import { createHash, timingSafeEqual } from "node:crypto";

export const TV_WEBHOOK_MAX_BYTES = 16 * 1024;

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

/** Constant-time match of `candidate` against every configured value. */
export function secretMatches(candidate: string, configured: string | undefined): boolean {
  const values = (configured ?? "").split(",").map((v) => v.trim()).filter(Boolean);
  if (!candidate || values.length === 0) return false;
  const got = digest(candidate);
  let ok = false;
  for (const value of values) {
    // No short-circuit: every configured value is compared.
    ok = timingSafeEqual(got, digest(value)) || ok;
  }
  return ok;
}

/** The body secret: the JSON `secret` field, else a `"secret":"…"` or
 * `secret=…` token in a text/plain body (TradingView sends text/plain when an
 * empty placeholder breaks the JSON). */
export function extractBodySecret(raw: string): string {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && typeof (parsed as { secret?: unknown }).secret === "string") {
      return (parsed as { secret: string }).secret;
    }
  } catch {
    // fall through to the text forms
  }
  const match = raw.match(/"secret"\s*:\s*"([^"]*)"/) ?? raw.match(/\bsecret=(\S+)/);
  return match?.[1] ?? "";
}

export type TvAlertFields = {
  symbol: string | null;
  exchange: string | null;
  price: number | null;
  interval: string | null;
  alert_name: string | null;
  bar_time: string | null;
  sent_at: string | null;
};

function str(value: unknown): string | null {
  if (typeof value === "number") return String(value);
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function num(value: unknown): number | null {
  const s = str(value);
  if (s === null) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** Never throws. `fields` is null when the body is not a JSON object. The
 * secret is deliberately not among the returned fields. */
export function parseTvAlertBody(raw: string): { fields: TvAlertFields | null; error: string | null } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    return { fields: null, error: `invalid json: ${err instanceof Error ? err.message : String(err)}`.slice(0, 200) };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { fields: null, error: "invalid json: not an object" };
  }
  const o = parsed as Record<string, unknown>;
  return {
    fields: {
      symbol: str(o.symbol),
      exchange: str(o.exchange),
      price: num(o.price),
      interval: str(o.interval),
      alert_name: str(o.alert),
      bar_time: str(o.bar_time),
      sent_at: str(o.sent_at),
    },
    error: null,
  };
}
