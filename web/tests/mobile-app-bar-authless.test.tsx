/**
 * @vitest-environment jsdom
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MobileAppBar from "../components/mobile/MobileAppBar";

vi.mock("@clerk/nextjs", () => ({
  useUser: () => ({ user: { primaryEmailAddress: { emailAddress: "operator@example.test" } } }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

vi.mock("@/lib/IBStatusContext", () => ({
  useIBStatusContext: () => ({ displayStatus: "connected" }),
}));

vi.mock("@/lib/useProfile", () => ({
  useProfile: () => ({ profile: { username: "Operator", avatar_url: null } }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("MobileAppBar authenticated rendering", () => {
  it("renders identity exclusively from Clerk-backed hooks", () => {
    render(<MobileAppBar title="Dashboard" onOpenSearch={() => undefined} />);

    expect(screen.getByTestId("mobile-app-bar")).toBeTruthy();
    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(screen.getByLabelText("IB Gateway connected")).toBeTruthy();
    expect(screen.getByTestId("mobile-app-bar-profile").textContent).toContain("OP");
  });
});
