/**
 * One-shot setup token (the Jupyter pattern).
 *
 * The /setup wizard is reachable without auth by construction — there is no
 * auth yet — so possession of the terminal that launched Radon is the
 * credential: the token prints to that console on first use and every setup
 * API call must present it. `RADON_SETUP_TOKEN` overrides for automation.
 *
 * Node runtime only (route handlers). The middleware never imports this.
 */

import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

// Wrong-token budget per process. The token is 192 bits so guessing is
// hopeless anyway; the limiter turns an unbounded online oracle into ten
// tries per window and makes a scripted sweep visible as 429s.
const MAX_FAILURES = 10;
const FAILURE_WINDOW_MS = 15 * 60_000;

let generated: string | null = null;
let failures: number[] = [];
let consumed = false;

export function getSetupToken(): string {
  const fromEnv = (process.env.RADON_SETUP_TOKEN || "").trim();
  if (fromEnv) return fromEnv;
  if (!generated) {
    generated = randomBytes(24).toString("hex");
    console.log(
      [
        "",
        "[radon setup] ────────────────────────────────────────────",
        `[radon setup] First-run setup token: ${generated}`,
        "[radon setup] Open /setup in the browser and paste it there.",
        "[radon setup] ────────────────────────────────────────────",
        "",
      ].join("\n"),
    );
  }
  return generated;
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

/**
 * Pure constant-time comparison. Both sides are hashed to a fixed width first
 * so a length mismatch takes the same path as a content mismatch instead of
 * returning early.
 */
export function verifySetupToken(provided: unknown): boolean {
  if (consumed) return false;
  if (typeof provided !== "string" || !provided.trim()) return false;
  return timingSafeEqual(digest(getSetupToken()), digest(provided.trim()));
}

/** One-shot: invalidate the token after a successful wizard completion. */
export function consumeSetupToken(): void {
  consumed = true;
}

function pruneFailures(now: number): void {
  failures = failures.filter((at) => now - at < FAILURE_WINDOW_MS);
}

/**
 * Route chokepoint: the 401 / 429 response for a bad or locked-out token, or
 * null when the token verifies. Failures count only while the limiter is
 * open, so the lockout drains exactly FAILURE_WINDOW_MS after the tenth miss.
 */
export function setupTokenRejection(provided: unknown, requestId: string): Response | null {
  const now = Date.now();
  pruneFailures(now);
  if (failures.length >= MAX_FAILURES) {
    const retryAfterSec = Math.max(1, Math.ceil((failures[0] + FAILURE_WINDOW_MS - now) / 1000));
    const response = jsonApiError({
      message: "Too many setup token attempts. Wait and retry.",
      status: 429,
      code: "RATE_LIMITED",
      requestId,
    });
    response.headers.set("Retry-After", String(retryAfterSec));
    return setNoStoreResponseHeaders(response, requestId);
  }
  if (verifySetupToken(provided)) return null;
  failures.push(now);
  return setNoStoreResponseHeaders(
    jsonApiError({
      message: "Setup token mismatch. It is printed in the terminal that launched Radon.",
      status: 401,
      code: "SETUP_TOKEN_INVALID",
      requestId,
    }),
    requestId,
  );
}

/** Test-only: reset the generated token, consumption, and the failure budget. */
export function __resetSetupTokenForTests(): void {
  generated = null;
  consumed = false;
  failures = [];
}
