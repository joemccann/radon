/**
 * @vitest-environment jsdom
 *
 * Audit 2026-11-03 §3.5: DemoPendingRetry called router.replace("/portfolio")
 * on EVERY tick while the trial was still pending, so the gate bounced the
 * visitor straight back on each attempt. It now navigates only once the
 * refreshed session token carries a resolved (non-pending) trial claim.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const getToken = vi.fn();
const replace = vi.fn();
const refresh = vi.fn();
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken, isLoaded: true }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh }) }));

import DemoPendingRetry from "@/app/demo-pending/DemoPendingRetry";

function jwt(metadata: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64({ alg: "RS256" })}.${b64({ sub: "user_1", metadata })}.sig`;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  getToken.mockReset();
  replace.mockReset();
  refresh.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

async function tick() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
}

describe("DemoPendingRetry", () => {
  it("stays put while the token is still pending, then navigates once the trial claim lands", async () => {
    getToken.mockResolvedValue(jwt({ demoRole: "pending" }));
    act(() => root.render(<DemoPendingRetry />));

    await tick();
    await tick();
    expect(getToken).toHaveBeenCalledTimes(2);
    expect(replace).not.toHaveBeenCalled();
    expect(container.textContent).toContain("(3/12)");

    getToken.mockResolvedValue(
      jwt({ demoRole: "trial", demoTrialExpiresAt: "2099-01-02T16:00:00-05:00" }),
    );
    await tick();
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/portfolio");
  });

  it("a token that cannot be read counts as still pending", async () => {
    getToken.mockResolvedValue(null);
    act(() => root.render(<DemoPendingRetry />));
    await tick();
    getToken.mockResolvedValue("not-a-jwt");
    await tick();
    expect(replace).not.toHaveBeenCalled();
  });
});
