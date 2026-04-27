import { describe, expect, it } from "vitest";

import { isLocalAuthlessTestBypassEnabled } from "../middleware";

describe("authless middleware bypass", () => {
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
