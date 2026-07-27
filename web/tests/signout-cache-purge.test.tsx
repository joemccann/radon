/** @vitest-environment jsdom */
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ useAuth: vi.fn() }));
vi.mock("@clerk/nextjs", () => ({ useAuth: mocks.useAuth }));

import SignOutCachePurge from "../components/SignOutCachePurge";

afterEach(() => {
  cleanup();
  mocks.useAuth.mockReset();
  vi.unstubAllEnvs();
});

describe("SignOutCachePurge", () => {
  it("does not call Clerk hooks when authless mode omits ClerkProvider", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_AUTHLESS_TEST", "1");
    mocks.useAuth.mockImplementation(() => {
      throw new Error("useAuth must not run without ClerkProvider");
    });

    render(<SignOutCachePurge />);

    expect(mocks.useAuth).not.toHaveBeenCalled();
  });
});
