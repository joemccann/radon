import { describe, it, expect } from "vitest";
import { DEMO_URL } from "./editorial-content";
import { DEMO_APP_URL } from "./seo";

/**
 * Every "try the demo" CTA on radon.run resolves to DEMO_URL. The bare origin
 * is an authenticated route that 404s a signed-out visitor, so the CTA has to
 * land on the sign-up entry point instead — the bare origin stays the site's
 * identity URL for JSON-LD.
 */
describe("demo CTA destination", () => {
  it("points at the public sign-up entry, not the gated root", () => {
    expect(DEMO_URL).toBe("https://demo.radon.run/sign-up");
    expect(new URL(DEMO_URL).pathname).not.toBe("/");
  });

  it("keeps the bare origin as the structured-data identity", () => {
    expect(DEMO_APP_URL).toBe("https://demo.radon.run");
  });
});
