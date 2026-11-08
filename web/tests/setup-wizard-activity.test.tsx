/**
 * @vitest-environment jsdom
 *
 * SetupWizard collect step as one activity machine (idle | validating |
 * completing):
 *   - a vendor verdict belongs to the values it checked; editing a field
 *     clears that service's verdict;
 *   - Store everything is disarmed while a vendor check is in flight and
 *     sends nothing on the wire.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import SetupWizard from "../components/setup/SetupWizard";

const FIELD = {
  name: "CLERK_SECRET_KEY",
  label: "Secret key",
  secret: true,
  placeholder: "sk_live_...",
  configured: false,
  hint: "",
  version: 0,
  updated_at: null,
  updated_by: null,
  env_fallback: false,
};

const STATUS = {
  ok: true,
  backend: true,
  credentials: {
    groups: ["Infrastructure"],
    services: [
      { id: "clerk", label: "Clerk", group: "Infrastructure", validator: true, slow: false, note: "", fields: [FIELD] },
    ],
    generated_at: "2026-09-01T00:00:00Z",
  },
};

type Call = { url: string; method: string; body: unknown };

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function stub(validate: () => Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url === "/api/setup/status") return Promise.resolve(json(STATUS));
    if (url === "/api/setup/validate") return validate();
    if (url === "/api/setup/complete") {
      return Promise.resolve(json({ ok: true, backend: true, outcomes: [], written: [], restart_required: true }));
    }
    throw new Error(`unexpected ${url}`);
  }));
  return calls;
}

async function reachCollect() {
  render(<SetupWizard />);
  fireEvent.change(screen.getByTestId("setup-token-input"), { target: { value: "tok-abc" } });
  fireEvent.click(screen.getByTestId("setup-token-submit"));
  await screen.findByTestId("setup-service-clerk");
  fireEvent.change(document.getElementById("setup-CLERK_SECRET_KEY")!, { target: { value: "sk_live_one" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SetupWizard activity machine", () => {
  it("clears a service verdict when its draft is edited", async () => {
    stub(async () => json({ validation: { status: "valid", message: "Clerk accepted the key" } }));
    await reachCollect();
    fireEvent.click(screen.getByTestId("setup-validate-clerk"));
    await waitFor(() => expect(screen.getByTestId("setup-verdict-clerk").textContent).toContain("accepted"));

    fireEvent.change(document.getElementById("setup-CLERK_SECRET_KEY")!, { target: { value: "sk_live_two" } });
    expect(screen.queryByTestId("setup-verdict-clerk")).toBeNull();
  });

  it("does not complete while a vendor check is in flight", async () => {
    let release: (r: Response) => void = () => {};
    const calls = stub(() => new Promise<Response>((resolve) => { release = resolve; }));
    await reachCollect();
    fireEvent.click(screen.getByTestId("setup-validate-clerk"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/setup/validate")).toBe(true));

    const complete = screen.getByTestId("setup-complete") as HTMLButtonElement;
    expect(complete.disabled).toBe(true);
    fireEvent.click(complete);
    expect(calls.filter((c) => c.url === "/api/setup/complete")).toHaveLength(0);

    release(json({ validation: { status: "valid", message: "ok" } }));
    await waitFor(() => expect((screen.getByTestId("setup-complete") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("setup-complete"));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/setup/complete")).toHaveLength(1));
    const sent = calls.find((c) => c.url === "/api/setup/complete")!;
    expect(sent.method).toBe("POST");
    expect(sent.body).toEqual({ token: "tok-abc", services: { clerk: { CLERK_SECRET_KEY: "sk_live_one" } } });
  });
});
