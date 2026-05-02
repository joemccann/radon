import { describe, expect, it } from "vitest";

import {
  isLocalAuthlessTestBypassEnabled,
  isLocalDevAuthBypassEnabled,
} from "../middleware";

describe("isLocalAuthlessTestBypassEnabled (explicit test flag)", () => {
  it("bypasses auth only when localhost and explicit test flag are enabled", () => {
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://localhost:3000/portfolio"), "1"),
    ).toBe(true);
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://127.0.0.1:3000/portfolio"), "1"),
    ).toBe(true);
  });

  it("does not bypass auth without the explicit test flag", () => {
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://localhost:3000/portfolio"), undefined),
    ).toBe(false);
  });

  it("does not bypass auth for non-local hosts", () => {
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("https://radon.run/portfolio"), "1"),
    ).toBe(false);
  });
});

describe("isLocalDevAuthBypassEnabled (auto-bypass for `next dev`)", () => {
  it("bypasses auth on localhost / 127.0.0.1 / ::1 in development", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("http://localhost:3000/portfolio"), "development")).toBe(true);
    expect(isLocalDevAuthBypassEnabled(new URL("http://127.0.0.1:3000/portfolio"), "development")).toBe(true);
    expect(isLocalDevAuthBypassEnabled(new URL("http://[::1]:3000/portfolio"), "development")).toBe(true);
  });

  it("does NOT bypass auth in production, even on localhost", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("http://localhost:3000/portfolio"), "production")).toBe(false);
  });

  it("does NOT bypass auth for non-local hosts in development", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("https://radon.run/portfolio"), "development")).toBe(false);
  });

  it("treats undefined NODE_ENV as non-production (Node REPL / vitest default)", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("http://localhost:3000/portfolio"), undefined)).toBe(true);
  });

  it("treats 'test' (vitest) as non-production", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("http://localhost:3000/portfolio"), "test")).toBe(true);
  });
});
