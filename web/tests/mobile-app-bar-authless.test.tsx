/**
 * @vitest-environment jsdom
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MobileAppBar from "../components/mobile/MobileAppBar";

vi.mock("@clerk/nextjs", () => ({
  useUser: () => {
    throw new Error("useUser must not be called in authless mobile tests");
  },
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

vi.mock("@/lib/IBStatusContext", () => ({
  useIBStatusContext: () => ({ displayStatus: "connected" }),
}));

vi.mock("@/lib/useProfile", () => ({
  useProfile: () => {
    throw new Error("useProfile must not be called in authless mobile tests");
  },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("MobileAppBar authless rendering", () => {
  it("does not call Clerk hooks when the authless test flag removes ClerkProvider", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_AUTHLESS_TEST", "1");

    render(<MobileAppBar title="Dashboard" onOpenSearch={() => undefined} />);

    expect(screen.getByTestId("mobile-app-bar")).toBeTruthy();
    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(screen.getByLabelText("IB Gateway connected")).toBeTruthy();
    expect(screen.getByTestId("mobile-app-bar-profile").textContent).toContain("OP");
  });
});
