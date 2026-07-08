// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import React from "react";

const useUserMock = vi.fn();
vi.mock("@clerk/nextjs", () => ({
  useUser: () => useUserMock(),
}));

import DemoWelcomeModal, { formatExpiryEt } from "@/components/DemoWelcomeModal";

// Window-relative, never hardcoded: a fixed "future" date rots into the
// past and silently flips the active-trial tests to the expired path
// (exactly what happened when 2026-07-07 passed).
const FUTURE = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
const PAST = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();

function clerkUser(publicMetadata: Record<string, unknown> | undefined, id = "user_1") {
  return { isLoaded: true, user: { id, publicMetadata } };
}

describe("DemoWelcomeModal", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    cleanup();
  });

  it("shows the expiry for an active demo trial user", () => {
    useUserMock.mockReturnValue(
      clerkUser({ demoRole: "trial", demoTrialExpiresAt: FUTURE }),
    );
    render(<DemoWelcomeModal />);
    expect(screen.getByTestId("demo-expiry").textContent).toBe(formatExpiryEt(FUTURE));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renders nothing for a non-demo user", () => {
    useUserMock.mockReturnValue(clerkUser({}));
    render(<DemoWelcomeModal />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders nothing for an expired trial (the gate owns that path)", () => {
    useUserMock.mockReturnValue(
      clerkUser({ demoRole: "trial", demoTrialExpiresAt: PAST }),
    );
    render(<DemoWelcomeModal />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows only once per browser session per user", () => {
    useUserMock.mockReturnValue(
      clerkUser({ demoRole: "trial", demoTrialExpiresAt: FUTURE }),
    );
    const first = render(<DemoWelcomeModal />);
    expect(screen.getByRole("dialog")).toBeTruthy();
    first.unmount();
    render(<DemoWelcomeModal />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders nothing outside the demo deployment", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    useUserMock.mockReturnValue(
      clerkUser({ demoRole: "trial", demoTrialExpiresAt: FUTURE }),
    );
    render(<DemoWelcomeModal />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("formatExpiryEt", () => {
  it("formats an ISO timestamp as an ET wall-clock string", () => {
    expect(formatExpiryEt("2026-07-07T16:00:00-04:00")).toBe(
      "Tuesday, July 7 at 4:00 PM ET",
    );
  });
  it("returns a placeholder for garbage input", () => {
    expect(formatExpiryEt("not-a-date")).toBe("---");
  });
});
