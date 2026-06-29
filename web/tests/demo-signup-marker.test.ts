import { describe, it, expect } from "vitest";

import { shouldMarkDemoSignup } from "../lib/demo/demoSignup";

/**
 * The /sign-up page is shared between app.radon.run and demo.radon.run. Only the
 * demo deployment sets NEXT_PUBLIC_RADON_DEMO=1, and only there should the new
 * user be stamped with unsafeMetadata.demo=true (the marker the Clerk
 * user.created webhook keys on to provision a trial). Prod must NOT mark signups.
 */
describe("shouldMarkDemoSignup", () => {
  it("marks the signup only when NEXT_PUBLIC_RADON_DEMO is exactly '1'", () => {
    expect(shouldMarkDemoSignup("1")).toBe(true);
  });

  it("does not mark on prod (flag unset)", () => {
    expect(shouldMarkDemoSignup(undefined)).toBe(false);
    expect(shouldMarkDemoSignup("")).toBe(false);
  });

  it("is strict — only '1', not other truthy strings", () => {
    expect(shouldMarkDemoSignup("0")).toBe(false);
    expect(shouldMarkDemoSignup("true")).toBe(false);
    expect(shouldMarkDemoSignup("yes")).toBe(false);
  });
});
