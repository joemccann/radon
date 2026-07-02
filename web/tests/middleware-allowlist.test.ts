/**
 * Authorization layer (operator allowlist) on top of authentication.
 *
 * Incident 2026-06-27: app.radon.run authorized ANY authenticated Clerk session
 * — there was no per-user gate in the Next.js perimeter (the ALLOWED_USER_IDS
 * allowlist lived only in FastAPI, and is bypassed for trusted-local Next→API
 * calls). Two non-operator users in the production Clerk instance could
 * therefore reach the operator's real portfolio/journal/orders.
 *
 * The fix: a valid Clerk session is necessary but NOT sufficient. When
 * ALLOWED_USER_IDS is set (production), only those operator ids may proceed;
 * every other signed-in user is forbidden. Empty/unset => no enforcement, so
 * local dev, CI, and the (separately-gated) demo deployment are unaffected.
 *
 * These tests exercise the pure decision functions; end-to-end validation is a
 * post-deploy curl against app.radon.run.
 */
import { describe, expect, it } from "vitest";

import {
  isAuthorizedUser,
  parseAllowedUserIds,
  requiresOperatorAllowlist,
} from "../middleware";

describe("operator allowlist — authorization layer", () => {
  it("fails open when ALLOWED_USER_IDS is unset/empty (dev, CI, demo)", () => {
    expect(isAuthorizedUser("user_anything", undefined)).toBe(true);
    expect(isAuthorizedUser("user_anything", "")).toBe(true);
    expect(isAuthorizedUser("user_anything", "   ")).toBe(true);
  });

  it("admits only the listed operator ids when the allowlist is set", () => {
    const raw = "user_operator1, user_operator2";
    expect(isAuthorizedUser("user_operator1", raw)).toBe(true);
    expect(isAuthorizedUser("user_operator2", raw)).toBe(true);
  });

  it("forbids a signed-in but non-allowlisted user (the incident case)", () => {
    const raw = "user_operator1";
    expect(isAuthorizedUser("user_intruder", raw)).toBe(false);
  });

  it("is exact-match, not substring — no access via prefix/suffix", () => {
    const raw = "user_operator";
    expect(isAuthorizedUser("user_operator_evil", raw)).toBe(false);
    expect(isAuthorizedUser("user_operato", raw)).toBe(false);
  });

  it("parses comma/space separated ids robustly", () => {
    expect(parseAllowedUserIds("a, b ,c,")).toEqual(new Set(["a", "b", "c"]));
    expect(parseAllowedUserIds(" ")).toEqual(new Set());
    expect(parseAllowedUserIds(undefined).size).toBe(0);
  });
});

// The production fail-closed interlock. Default behavior (interlock OFF) is the
// existing fail-open; production sets RADON_REQUIRE_OPERATOR_ALLOWLIST=1 so a
// blanked ALLOWED_USER_IDS denies everyone instead of re-opening the app.
describe("operator allowlist — production fail-closed interlock", () => {
  it("requiresOperatorAllowlist is true only for the literal '1'", () => {
    expect(requiresOperatorAllowlist("1")).toBe(true);
    expect(requiresOperatorAllowlist("0")).toBe(false);
    expect(requiresOperatorAllowlist("true")).toBe(false);
    expect(requiresOperatorAllowlist(undefined)).toBe(false);
    expect(requiresOperatorAllowlist("")).toBe(false);
  });

  it("interlock OFF (default) keeps failing OPEN on an empty allowlist", () => {
    expect(isAuthorizedUser("user_anything", "", false)).toBe(true);
    expect(isAuthorizedUser("user_anything", undefined, false)).toBe(true);
    expect(isAuthorizedUser("user_anything", "  ", false)).toBe(true);
  });

  it("interlock ON fails CLOSED on an empty allowlist — denies EVERY user incl. the operator", () => {
    expect(isAuthorizedUser("user_operator", "", true)).toBe(false);
    expect(isAuthorizedUser("user_operator", undefined, true)).toBe(false);
    expect(isAuthorizedUser("user_operator", "   ", true)).toBe(false);
  });

  it("interlock ON is a no-op once the allowlist IS populated (operator passes, intruder denied)", () => {
    const raw = "user_operator1, user_operator2";
    expect(isAuthorizedUser("user_operator1", raw, true)).toBe(true);
    expect(isAuthorizedUser("user_operator2", raw, true)).toBe(true);
    expect(isAuthorizedUser("user_intruder", raw, true)).toBe(false);
  });
});
