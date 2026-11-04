/**
 * @vitest-environment jsdom
 *
 * DemoUsersTable actions: busy state is per user (one action finishing must
 * not unlock another user's in-flight row), and Revoke is gated behind a
 * confirm. Asserted at the wire: full URL, method, payload, and nothing sent
 * while the confirm is still closed.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DemoUsersTable from "../components/admin/DemoUsersTable";

const FUTURE = new Date(Date.now() + 3 * 86_400_000).toISOString();

function user(id: string) {
  return {
    user_id: id,
    email: `${id}@example.com`,
    demo_role: "trial",
    started_at: "2026-11-01T00:00:00Z",
    expires_at: FUTURE,
    status: "active",
    revoked_at: null,
    created_at: "2026-11-01T00:00:00Z",
    aiUsage: {},
  };
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

type Deferred = { resolve: (r: Response) => void };

function setup() {
  vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
  const posts: Array<{ url: string; init: RequestInit; deferred: Deferred }> = [];
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return new Promise<Response>((resolve) => {
        posts.push({ url, init, deferred: { resolve } });
      });
    }
    return Promise.resolve(json({ users: [user("u_a"), user("u_b")] }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<DemoUsersTable />);
  return { fetchMock, posts };
}

function rowButton(id: string, name: RegExp) {
  return within(screen.getByTestId(`demo-user-${id}`)).getByRole("button", { name }) as HTMLButtonElement;
}

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("DemoUsersTable actions", () => {
  it("keeps a user busy until its own action settles, not another user's", async () => {
    const { posts } = setup();
    await screen.findByTestId("demo-user-u_a");

    fireEvent.click(rowButton("u_a", /extend/i));
    fireEvent.click(rowButton("u_b", /extend/i));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(rowButton("u_a", /extend/i).disabled).toBe(true);
    expect(rowButton("u_b", /extend/i).disabled).toBe(true);

    posts[1].deferred.resolve(json({ ok: true }));
    await waitFor(() => expect(rowButton("u_b", /extend/i).disabled).toBe(false));
    expect(rowButton("u_a", /extend/i).disabled).toBe(true);

    posts[0].deferred.resolve(json({ ok: true }));
    await waitFor(() => expect(rowButton("u_a", /extend/i).disabled).toBe(false));
  });

  it("does not revoke until the operator confirms, then posts the revoke", async () => {
    const { fetchMock, posts } = setup();
    await screen.findByTestId("demo-user-u_a");

    fireEvent.click(rowButton("u_a", /revoke/i));
    const dialog = await screen.findByTestId("admin-confirm");
    expect(posts).toHaveLength(0);

    fireEvent.click(within(dialog).getByTestId("admin-confirm-action"));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toBe("/api/admin/demo-users");
    expect(posts[0].init.method).toBe("POST");
    expect(JSON.parse(String(posts[0].init.body))).toEqual({ action: "revoke", userId: "u_a" });
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("sends nothing when the revoke confirm is cancelled", async () => {
    const { posts } = setup();
    await screen.findByTestId("demo-user-u_a");

    fireEvent.click(rowButton("u_a", /revoke/i));
    const dialog = await screen.findByTestId("admin-confirm");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId("admin-confirm")).toBeNull());
    expect(posts).toHaveLength(0);
  });
});
