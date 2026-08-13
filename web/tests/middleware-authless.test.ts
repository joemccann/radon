import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import {
  isAuthlessTestBypassEnabled,
} from "../middleware";

describe("isAuthlessTestBypassEnabled", () => {
  it("requires an explicit flag plus an unforgeable matching request token", () => {
    expect(isAuthlessTestBypassEnabled("secret", "secret", "1")).toBe(true);
  });

  it("fails closed for absent, wrong, or unconfigured tokens", () => {
    expect(isAuthlessTestBypassEnabled(null, "secret", "1")).toBe(false);
    expect(isAuthlessTestBypassEnabled("wrong", "secret", "1")).toBe(false);
    expect(isAuthlessTestBypassEnabled("secret", undefined, "1")).toBe(false);
    expect(isAuthlessTestBypassEnabled("secret", "secret", undefined)).toBe(false);
  });

  it("does not trust a spoofed Host header", () => {
    const request = new NextRequest("http://0.0.0.0:3000/portfolio", {
      headers: { host: "localhost:3000" },
    });
    expect(isAuthlessTestBypassEnabled(
      request.headers.get("x-radon-authless-test"),
      "secret",
      "1",
    )).toBe(false);
  });
});
