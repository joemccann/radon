/**
 * @vitest-environment jsdom
 *
 * IBKR operator hold, asserted at the wire.
 *
 * Hold stops the broker Gateway and keeps every recovery path from logging it
 * back in, so the operator can flatten from IBKR Mobile with the username the
 * Gateway shares. Resume logs the Gateway in once (one 2FA push). Both are
 * gated: Hold behind a reason plus a typed HOLD, Resume behind a confirm.
 * <IbOperatorHold /> owns the fetch, so it is rendered directly and every
 * request is recorded: nothing while a gate is closed, exactly ONE POST on the
 * full path with the exact payload once armed. The route proxy is pinned the
 * same way at the bottom.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import IbOperatorHold from "../components/admin/IbOperatorHold";

type Call = { url: string; method: string; body: unknown; cache?: RequestCache };

function recordFetch(reply: unknown = { ok: true, detail: "hold set; Gateway stopped." }, status = 200): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: typeof input === "string" ? input : input.toString(),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body,
        cache: init?.cache,
      });
      return new Response(JSON.stringify(reply), { status, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

async function flush() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

async function click(testId: string) {
  await act(async () => {
    fireEvent.click(screen.getByTestId(testId));
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

async function type(testId: string, value: string) {
  await act(async () => {
    fireEvent.change(screen.getByTestId(testId), { target: { value } });
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Hold Gateway", () => {
  it("sends nothing until a reason is entered and HOLD is typed, then exactly one POST", async () => {
    const calls = recordFetch();
    render(<IbOperatorHold hold={{ held: false }} />);
    await flush();

    const holdButton = screen.getByTestId("ib-operator-hold-set") as HTMLButtonElement;
    expect(holdButton.disabled).toBe(true);
    await click("ib-operator-hold-set");
    expect(calls).toHaveLength(0);

    await type("ib-operator-hold-reason", "flatten on IBKR Mobile");
    expect(holdButton.disabled).toBe(false);
    await click("ib-operator-hold-set");
    expect(screen.getByTestId("admin-confirm")).toBeTruthy();

    const confirm = screen.getByTestId("admin-confirm-action") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    await click("admin-confirm-action");
    expect(calls).toHaveLength(0);

    await type("admin-confirm-typed-input", "HOLD");
    expect(calls).toHaveLength(0);
    await click("admin-confirm-action");

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/admin/ib/operator-hold");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].cache).toBe("no-store");
    expect(calls[0].body).toEqual({ held: true, reason: "flatten on IBKR Mobile" });
    expect(screen.getByTestId("ib-operator-hold-result").textContent).toContain("hold set");
  });

  it("sends nothing when the hold confirm is cancelled", async () => {
    const calls = recordFetch();
    render(<IbOperatorHold hold={{ held: false }} />);
    await type("ib-operator-hold-reason", "flatten");
    await click("ib-operator-hold-set");
    const cancel = screen.getByTestId("admin-confirm").querySelector<HTMLButtonElement>(".admin-btn-ghost");
    await act(async () => {
      fireEvent.click(cancel!);
      await Promise.resolve();
    });
    expect(calls).toHaveLength(0);
  });

  it("is disabled while the hold state is unknown (broker unreachable)", async () => {
    const calls = recordFetch();
    render(<IbOperatorHold hold={null} />);
    await type("ib-operator-hold-reason", "flatten");
    expect((screen.getByTestId("ib-operator-hold-set") as HTMLButtonElement).disabled).toBe(true);
    await click("ib-operator-hold-set");
    expect(calls).toHaveLength(0);
  });
});

describe("Resume Gateway", () => {
  const HELD = { held: true, actor: "ssh:joe@phone", held_at: "2026-10-01T14:00:00+00:00", reason: "flatten" };

  it("shows who and why, then sends exactly one clear after confirming", async () => {
    const calls = recordFetch({ ok: true, detail: "hold cleared; approve the IBKR Mobile push" });
    render(<IbOperatorHold hold={HELD} />);
    expect(screen.getByTestId("ib-operator-hold-state").textContent).toBe("HELD");
    expect(screen.getByTestId("ib-operator-hold-detail").textContent).toContain("ssh:joe@phone");

    await click("ib-operator-hold-resume");
    expect(calls).toHaveLength(0);
    await click("admin-confirm-action");

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/admin/ib/operator-hold");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({ held: false });
  });

  it("surfaces a broker refusal instead of claiming success", async () => {
    recordFetch({ error: { message: "broker unreachable" } }, 504);
    render(<IbOperatorHold hold={HELD} />);
    await click("ib-operator-hold-resume");
    await click("admin-confirm-action");
    expect(screen.getByTestId("ib-operator-hold-error").textContent).toBeTruthy();
    expect(screen.queryByTestId("ib-operator-hold-result")).toBeNull();
  });
});

describe("POST /api/admin/ib/operator-hold proxy", () => {
  const mockRadonFetch = vi.fn();
  const mockAccess = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockAccess.mockReset();
    vi.doMock("@/lib/radonApi", () => ({
      radonFetch: mockRadonFetch,
      RadonApiError: class RadonApiError extends Error {
        status: number;
        constructor(status: number, detail: string) {
          super(detail);
          this.status = status;
        }
      },
    }));
    vi.doMock("@/lib/routeAccess", () => ({ requireRouteAccess: mockAccess }));
    mockAccess.mockResolvedValue({ ok: true, principal: { userId: "u", kind: "operator", token: "jwt" } });
  });

  function post(body: unknown) {
    return new Request("http://localhost/api/admin/ib/operator-hold", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  it("forwards an armed hold to FastAPI with the operator JWT", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true });
    const { POST } = await import("../app/api/admin/ib/operator-hold/route");
    const res = await POST(post({ held: true, reason: "flatten", extra: "dropped" }));
    expect(res.status).toBe(200);
    expect(mockRadonFetch).toHaveBeenCalledTimes(1);
    const [path, opts] = mockRadonFetch.mock.calls[0];
    expect(path).toBe("/ib/operator-hold");
    expect(opts.method).toBe("POST");
    expect(opts.token).toBe("jwt");
    expect(JSON.parse(opts.body)).toEqual({ held: true, reason: "flatten" });
    expect(opts.timeout).toBeGreaterThan(135_000);
  });

  it.each([[{}], [{ held: "yes" }], [{ held: true }], [{ held: true, reason: "  " }]])(
    "rejects %j with 400 and sends nothing",
    async (body) => {
      const { POST } = await import("../app/api/admin/ib/operator-hold/route");
      const res = await POST(post(body));
      expect(res.status).toBe(400);
      expect(mockRadonFetch).not.toHaveBeenCalled();
    },
  );

  it("refuses a non-operator before any proxy call", async () => {
    mockAccess.mockResolvedValueOnce({ ok: false, response: new Response("{}", { status: 403 }) });
    const { POST } = await import("../app/api/admin/ib/operator-hold/route");
    const res = await POST(post({ held: false }));
    expect(res.status).toBe(403);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });
});
