/**
 * @vitest-environment jsdom
 */

import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const mockAuth = vi.fn();
const mockRedirect = vi.fn((url: string) => {
  throw new Error(`REDIRECT:${url}`);
});

vi.mock("@clerk/nextjs/server", () => ({
  auth: mockAuth,
}));

vi.mock("next/navigation", () => ({
  redirect: mockRedirect,
}));

vi.mock("@clerk/nextjs", () => ({
  SignIn: () => React.createElement("div", null, "SIGN_IN_COMPONENT"),
  SignUp: () => React.createElement("div", null, "SIGN_UP_COMPONENT"),
}));

describe("auth pages", () => {
  beforeEach(() => {
    mockAuth.mockReset();
    mockRedirect.mockClear();
  });

  it("redirects authenticated users away from sign-in", async () => {
    mockAuth.mockResolvedValue({ userId: "user_123" });
    const mod = await import("../app/sign-in/[[...sign-in]]/page");

    await expect(mod.default()).rejects.toThrow("REDIRECT:/portfolio");
    expect(mockRedirect).toHaveBeenCalledWith("/portfolio");
  });

  it("renders the SignIn component for signed-out users", async () => {
    mockAuth.mockResolvedValue({ userId: null });
    const mod = await import("../app/sign-in/[[...sign-in]]/page");

    const element = await mod.default();
    render(element as React.ReactElement);

    expect(screen.getByText("SIGN_IN_COMPONENT")).toBeTruthy();
  });

  it("redirects authenticated users away from sign-up", async () => {
    mockAuth.mockResolvedValue({ userId: "user_123" });
    const mod = await import("../app/sign-up/[[...sign-up]]/page");

    await expect(mod.default()).rejects.toThrow("REDIRECT:/portfolio");
    expect(mockRedirect).toHaveBeenCalledWith("/portfolio");
  });

  it("renders the SignUp component for signed-out users", async () => {
    mockAuth.mockResolvedValue({ userId: null });
    const mod = await import("../app/sign-up/[[...sign-up]]/page");

    const element = await mod.default();
    render(element as React.ReactElement);

    expect(screen.getByText("SIGN_UP_COMPONENT")).toBeTruthy();
  });
});
