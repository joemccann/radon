/**
 * @vitest-environment jsdom
 *
 * app.radon.run/admin mounts DemoUsersTable. DEMO_ADMIN_USER_IDS is unset on
 * that deployment, so GET /api/admin/demo-users 403s and Chrome logs
 * "Failed to load resource". The panel is supposed to stay off the operator
 * view. It must not issue the request unless this build is the demo deployment.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DemoUsersTable from "../components/admin/DemoUsersTable";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("DemoUsersTable", () => {
  it("does not request /api/admin/demo-users on the operator deployment", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<DemoUsersTable />);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(document.querySelector("[data-testid='demo-users']")).toBeNull();
  });

  it("loads trials only on the demo deployment", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ users: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<DemoUsersTable />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/admin/demo-users", { cache: "no-store" });
    });
    expect(document.querySelector("[data-testid='demo-users']")).not.toBeNull();
  });
});
