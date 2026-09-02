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

import { randomBytes, timingSafeEqual } from "node:crypto";

let generated: string | null = null;
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

export function verifySetupToken(provided: unknown): boolean {
  if (consumed) return false;
  if (typeof provided !== "string" || !provided.trim()) return false;
  const expected = Buffer.from(getSetupToken(), "utf8");
  const candidate = Buffer.from(provided.trim(), "utf8");
  if (expected.length !== candidate.length) return false;
  return timingSafeEqual(expected, candidate);
}

/** One-shot: invalidate the token after a successful wizard completion. */
export function consumeSetupToken(): void {
  consumed = true;
}

/** Test-only: reset the generated token between cases. */
export function __resetSetupTokenForTests(): void {
  generated = null;
  consumed = false;
}
