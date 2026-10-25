/**
 * @vitest-environment jsdom
 *
 * IB Gateway password rotation from the Credentials tab, asserted at the wire.
 *
 * Saving the ib_gateway service writes the new password on the broker and
 * restarts the Gateway once, which fires an IBKR Mobile 2FA push. That is a
 * gated action: Save opens a confirm step, Confirm stays disabled until the
 * operator acknowledges the push, and only an armed Confirm fires the PUT.
 * These tests render the component that OWNS the fetch (<CredentialsPanel />),
 * record every request, and assert both the closed gate (nothing fired) and
 * the armed gate (exactly one PUT on the full path, method and payload).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import CredentialsPanel from "../components/profile/CredentialsPanel";
import type { CredentialServiceEntry, CredentialsPayload } from "../lib/credentials";

const NEW_PASSWORD = "N3w$pa ss";

function field(name: string, label: string, secret: boolean) {
  return {
    name,
    label,
    secret,
    placeholder: "",
    configured: true,
    hint: "••••abcd",
    version: 1,
    updated_at: "2026-09-01T00:00:00Z",
    updated_by: "op-1",
    env_fallback: false,
    exported_only: false,
  };
}

const IB_SERVICE: CredentialServiceEntry = {
  id: "ib_gateway",
  label: "IB Gateway login",
  group: "Infrastructure",
  validator: false,
  slow: false,
  note: "Saving writes this password to the broker Gateway and restarts IB Gateway once.",
  fields: [field("TWS_USERID", "IBKR username", false), field("TWS_PASSWORD", "IBKR password", true)],
};

const ANTHROPIC: CredentialServiceEntry = {
  id: "anthropic",
  label: "Anthropic",
  group: "AI Providers",
  validator: true,
  slow: false,
  note: "",
  fields: [{ ...field("ANTHROPIC_API_KEY", "API key", true), configured: false, hint: "" }],
};

const PAYLOAD: CredentialsPayload = {
  groups: ["AI Providers", "Infrastructure"],
  services: [ANTHROPIC, IB_SERVICE],
  generated_at: "2026-09-01T00:00:00Z",
};

type RecordedCall = { url: string; method: string; body: unknown };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function applied(status: "applied" | "deferred" | "restart_failed", detail = "") {
  return () =>
    jsonResponse({
      service: IB_SERVICE,
      validation: { status: "unchecked", message: "no live check for this service" },
      rotation: { status, detail },
    });
}

let calls: RecordedCall[] = [];
let putResponse: () => Response | Promise<Response>;

beforeEach(() => {
  calls = [];
  putResponse = applied("applied", "Gateway restarted");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ url, method, body });
      if (method === "GET") return jsonResponse(PAYLOAD);
      if (method === "PUT") {
        if (url === "/api/credentials/anthropic") {
          return jsonResponse({ service: ANTHROPIC, validation: { status: "valid", message: "" } });
        }
        return putResponse();
      }
      throw new Error(`unexpected ${method} ${url}`);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const writes = () => calls.filter((c) => c.method !== "GET");

async function renderPanel() {
  render(<CredentialsPanel />);
  await waitFor(() => expect(screen.getByTestId("credential-service-ib-gateway")).toBeTruthy());
}

function typePassword(value = NEW_PASSWORD) {
  fireEvent.change(document.getElementById("cred-TWS_PASSWORD")!, { target: { value } });
}

function openConfirm() {
  fireEvent.click(screen.getByTestId("credential-save-ib-gateway"));
  return screen.getByTestId("credential-confirm-ib-gateway");
}

function arm() {
  fireEvent.click(screen.getByTestId("credential-confirm-ack-ib-gateway"));
}

function confirmButton(): HTMLButtonElement {
  return screen.getByTestId("credential-confirm-submit-ib-gateway") as HTMLButtonElement;
}

async function rotate(value = NEW_PASSWORD) {
  typePassword(value);
  openConfirm();
  arm();
  fireEvent.click(confirmButton());
  await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1));
}

describe("IB Gateway rotation gate", () => {
  it("labels the save as a Gateway restart", async () => {
    await renderPanel();
    expect(screen.getByTestId("credential-save-ib-gateway").textContent).toBe(
      "Save and restart Gateway",
    );
  });

  it("closed gate: Save opens the confirm step and fires nothing", async () => {
    await renderPanel();
    typePassword();
    openConfirm();
    expect(writes()).toHaveLength(0);
    expect(confirmButton().disabled).toBe(true);
    fireEvent.click(confirmButton());
    expect(writes()).toHaveLength(0);
  });

  it("armed gate: acknowledge then Confirm fires exactly one PUT with the full path and payload", async () => {
    await renderPanel();
    typePassword();
    openConfirm();
    expect(writes()).toHaveLength(0);
    arm();
    expect(confirmButton().disabled).toBe(false);
    fireEvent.click(confirmButton());
    await waitFor(() => expect(writes()).toHaveLength(1));
    const put = writes()[0];
    expect(put.url).toBe("/api/credentials/ib_gateway");
    expect(put.method).toBe("PUT");
    expect(put.body).toEqual({ values: { TWS_PASSWORD: NEW_PASSWORD } });
  });

  it("sends the password untrimmed so the server rejects edge whitespace instead of altering it", async () => {
    await renderPanel();
    await rotate(" padded ");
    expect(writes()[0].body).toEqual({ values: { TWS_PASSWORD: " padded " } });
  });

  it("confirm copy names the 2FA push and the restart, with no em dash", async () => {
    await renderPanel();
    typePassword();
    const region = openConfirm();
    const text = region.textContent ?? "";
    expect(text).toContain("IBKR Mobile");
    expect(text).toContain("2FA push");
    expect(text).toContain("restarts");
    expect(text).toContain("IBKR website first");
    expect(text).not.toContain("—");
    expect(screen.getByTestId("credential-confirm-ack-ib-gateway").closest("label")?.textContent).toContain(
      "I changed it on IBKR and will approve the push",
    );
  });

  it("cancel closes the confirm step, resets the acknowledgement, and fires nothing", async () => {
    await renderPanel();
    typePassword();
    openConfirm();
    arm();
    fireEvent.click(screen.getByTestId("credential-confirm-cancel-ib-gateway"));
    expect(screen.queryByTestId("credential-confirm-ib-gateway")).toBeNull();
    expect(writes()).toHaveLength(0);
    openConfirm();
    expect(confirmButton().disabled).toBe(true);
    expect((screen.getByTestId("credential-confirm-ack-ib-gateway") as HTMLInputElement).checked).toBe(false);
  });

  it("double-click on Confirm fires one PUT", async () => {
    let release: (r: Response) => void = () => {};
    putResponse = () => new Promise<Response>((resolve) => { release = resolve; });
    await renderPanel();
    typePassword();
    openConfirm();
    arm();
    const button = confirmButton();
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toContain("Do not resubmit");
    await act(async () => {
      release(applied("applied")());
    });
    expect(writes()).toHaveLength(1);
  });
});

describe("IB Gateway rotation outcomes", () => {
  it("applied: stored on the broker and the push is next", async () => {
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toBe(
        "Stored on the broker and the Gateway restarted. Approve the IBKR Mobile push to finish the login.",
      ),
    );
    expect(screen.queryByTestId("credential-confirm-ib-gateway")).toBeNull();
  });

  it("deferred: names the reason and says the next restart uses the new password", async () => {
    putResponse = applied("deferred", "IBKR operator hold active");
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toBe(
        "Stored on the broker. Gateway restart deferred: IBKR operator hold active. The next restart uses the new password.",
      ),
    );
  });

  it("restart failed: stored, restart once from /admin", async () => {
    putResponse = applied("restart_failed", "compose up failed");
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toContain(
        "Stored on the broker, but the Gateway restart failed: compose up failed.",
      ),
    );
    expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toContain("/admin");
  });

  it("409 shows the server message verbatim", async () => {
    const message = "Gateway login blocked: cooldown: stop ran 3s ago. Nothing was saved.";
    putResponse = () =>
      jsonResponse({ detail: { code: "GATEWAY_LOGIN_BLOCKED", message } }, 409);
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toContain(message),
    );
  });

  it("504 unknown outcome says not to resubmit", async () => {
    putResponse = () =>
      jsonResponse(
        {
          detail: {
            code: "GATEWAY_ROTATION_UNKNOWN",
            message: "Outcome unknown. Do not resubmit. Check the Gateway in /admin.",
          },
        },
        504,
      );
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toContain(
        "Do not resubmit",
      ),
    );
  });

  it.each([
    [
      "Next upstreamFailure 502 UPSTREAM_ERROR",
      () =>
        jsonResponse(
          { error: "fetch failed", code: "UPSTREAM_ERROR", requestId: "req-1" },
          502,
        ),
    ],
    ["empty Caddy 502", () => new Response("", { status: 502 })],
    ["504 with no code", () => new Response("", { status: 504 })],
    [
      "FastAPI 500 with a string detail",
      () => jsonResponse({ detail: "Internal Server Error" }, 500),
    ],
  ])("%s: outcome unknown, do not resubmit", async (_label, response) => {
    putResponse = response;
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toBe(
        "Outcome unknown. Do not resubmit. Check the Gateway in /admin.",
      ),
    );
    expect(writes()).toHaveLength(1);
  });

  it("a network failure after the PUT left is an unknown outcome", async () => {
    putResponse = () => Promise.reject(new TypeError("Failed to fetch"));
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toContain(
        "Do not resubmit",
      ),
    );
  });

  it("a 4xx the proxy refused before FastAPI keeps the generic error", async () => {
    putResponse = () =>
      jsonResponse({ error: "Invalid JSON body", code: "BAD_REQUEST", requestId: "r" }, 400);
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).not.toContain(
        "Do not resubmit",
      ),
    );
  });

  it("broker unreachable says nothing was saved", async () => {
    const message = "Broker unreachable. Nothing was saved.";
    putResponse = () =>
      jsonResponse({ detail: { code: "GATEWAY_BROKER_UNREACHABLE", message } }, 503);
    await renderPanel();
    await rotate();
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-ib-gateway").textContent).toBe(message),
    );
  });

  it("busy notice derives its bound from the client timeout", async () => {
    const { GATEWAY_BUSY_NOTICE, GATEWAY_ROTATE_TIMEOUT_MS } = await import("../lib/credentials");
    const minutes = Math.ceil(GATEWAY_ROTATE_TIMEOUT_MS / 60_000);
    expect(GATEWAY_BUSY_NOTICE).toContain(`up to ${minutes} minutes`);
    expect(GATEWAY_ROTATE_TIMEOUT_MS).toBeGreaterThanOrEqual(150_000 + 15_000);
  });

  it("resets the acknowledgement after every outcome", async () => {
    putResponse = () =>
      jsonResponse({ detail: { code: "GATEWAY_LOGIN_BLOCKED", message: "blocked" } }, 409);
    await renderPanel();
    await rotate();
    await waitFor(() => expect(screen.getByTestId("credential-notice-ib-gateway")).toBeTruthy());
    openConfirm();
    expect(confirmButton().disabled).toBe(true);
  });

  it("never renders the submitted password back into the DOM", async () => {
    await renderPanel();
    await rotate();
    await waitFor(() => expect(screen.getByTestId("credential-notice-ib-gateway")).toBeTruthy());
    expect(document.body.innerHTML).not.toContain(NEW_PASSWORD);
  });
});

describe("other services keep the single-click save", () => {
  it("anthropic saves with one click and no confirm step", async () => {
    await renderPanel();
    fireEvent.change(document.getElementById("cred-ANTHROPIC_API_KEY")!, {
      target: { value: "sk-ant-new" },
    });
    fireEvent.click(screen.getByTestId("credential-save-anthropic"));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      url: "/api/credentials/anthropic",
      method: "PUT",
      body: { values: { ANTHROPIC_API_KEY: "sk-ant-new" } },
    });
    expect(screen.queryByTestId("credential-confirm-anthropic")).toBeNull();
  });
});
