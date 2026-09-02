/**
 * @vitest-environment jsdom
 *
 * The profile Credentials tab, asserted at the wire.
 *
 * Saving a credential is a gated action that fires a network call, so per the
 * testing contract these tests render the component that OWNS the fetch
 * (<CredentialsPanel />), record every request, and assert:
 *   1. nothing fires while the gate is closed (no draft typed), and
 *   2. an armed submit fires exactly ONE request matched on the FULL path,
 *      method, and payload shape.
 * Plus the redaction contract: a submitted value never renders back into the
 * DOM, and a 422 CREDENTIAL_REJECTED shows the playful retry line.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import CredentialsPanel from "../components/profile/CredentialsPanel";
import type { CredentialsPayload } from "../lib/credentials";

const PAYLOAD: CredentialsPayload = {
  groups: ["Market Data", "AI Providers"],
  services: [
    {
      id: "unusual_whales",
      label: "Unusual Whales",
      group: "Market Data",
      validator: true,
      slow: false,
      note: "",
      fields: [
        {
          name: "UW_TOKEN",
          label: "API token",
          secret: true,
          placeholder: "uw_...",
          configured: true,
          hint: "\u2022\u2022\u2022\u2022cret",
          version: 3,
          updated_at: "2026-09-01T00:00:00Z",
          updated_by: "op-1",
          env_fallback: false,
        },
      ],
    },
    {
      id: "menthorq",
      label: "MenthorQ",
      group: "Market Data",
      validator: true,
      slow: true,
      note: "Checked with a real browser login. Expect up to a minute.",
      fields: [
        {
          name: "MENTHORQ_USER",
          label: "Email / username",
          secret: false,
          placeholder: "",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
        },
        {
          name: "MENTHORQ_PASS",
          label: "Password",
          secret: true,
          placeholder: "",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
        },
      ],
    },
    {
      id: "anthropic",
      label: "Anthropic",
      group: "AI Providers",
      validator: true,
      slow: false,
      note: "",
      fields: [
        {
          name: "ANTHROPIC_API_KEY",
          label: "API key",
          secret: true,
          placeholder: "sk-ant-...",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
        },
      ],
    },
  ],
  generated_at: "2026-09-01T00:00:00Z",
};

type RecordedCall = { url: string; method: string; body: unknown };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let calls: RecordedCall[] = [];
let putResponse: () => Response;

beforeEach(() => {
  calls = [];
  putResponse = () =>
    jsonResponse({
      service: PAYLOAD.services[2],
      validation: { status: "valid", message: "" },
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ url, method, body });
      if (method === "GET") return jsonResponse(PAYLOAD);
      if (method === "PUT") return putResponse();
      if (method === "DELETE")
        return jsonResponse({ removed: true, service: PAYLOAD.services[0] });
      throw new Error(`unexpected ${method} ${url}`);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderPanel() {
  render(<CredentialsPanel />);
  await waitFor(() =>
    expect(screen.getByTestId("credential-service-anthropic")).toBeTruthy(),
  );
}

describe("credentials panel wire contract", () => {
  it("loads via GET /api/credentials and fires nothing else", async () => {
    await renderPanel();
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ url: "/api/credentials", method: "GET" });
  });

  it("closed gate: save disabled with no draft, click fires nothing", async () => {
    await renderPanel();
    const save = screen.getByTestId("credential-save-anthropic") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("armed submit fires exactly one PUT with full path and payload", async () => {
    await renderPanel();
    fireEvent.change(screen.getByLabelText(/API key/, { selector: "#cred-ANTHROPIC_API_KEY" }), {
      target: { value: "sk-ant-brand-new" },
    });
    const save = screen.getByTestId("credential-save-anthropic") as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1),
    );
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toBe("/api/credentials/anthropic");
    expect(put.body).toEqual({ values: { ANTHROPIC_API_KEY: "sk-ant-brand-new" } });
  });

  it("multi-field service submits only non-empty drafts", async () => {
    await renderPanel();
    fireEvent.change(document.getElementById("cred-MENTHORQ_PASS")!, {
      target: { value: "new-password" },
    });
    fireEvent.click(screen.getByTestId("credential-save-menthorq"));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1),
    );
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toBe("/api/credentials/menthorq");
    expect(put.body).toEqual({ values: { MENTHORQ_PASS: "new-password" } });
  });

  it("slow service shows the scenic-route notice while in flight", async () => {
    let release: (r: Response) => void = () => {};
    putResponse = () => {
      throw new Error("unused");
    };
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (method === "GET") return jsonResponse(PAYLOAD);
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      },
    );
    await renderPanel();
    fireEvent.change(document.getElementById("cred-MENTHORQ_PASS")!, {
      target: { value: "pw" },
    });
    fireEvent.click(screen.getByTestId("credential-save-menthorq"));
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-menthorq").textContent).toMatch(
        /up to a minute/i,
      ),
    );
    release(
      jsonResponse({
        service: PAYLOAD.services[1],
        validation: { status: "valid", message: "" },
      }),
    );
  });

  it("422 CREDENTIAL_REJECTED stores nothing client-side and shows the playful line", async () => {
    putResponse = () =>
      jsonResponse(
        {
          detail: {
            code: "CREDENTIAL_REJECTED",
            service: "anthropic",
            status: "invalid",
            message: "Anthropic rejected the credential (HTTP 401)",
          },
        },
        422,
      );
    await renderPanel();
    fireEvent.change(document.getElementById("cred-ANTHROPIC_API_KEY")!, {
      target: { value: "sk-ant-bad" },
    });
    fireEvent.click(screen.getByTestId("credential-save-anthropic"));
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-anthropic").textContent).toMatch(
        /absolutely not/i,
      ),
    );
    // The rejected draft stays in the input for a retry; nothing was applied.
    expect(
      (document.getElementById("cred-ANTHROPIC_API_KEY") as HTMLInputElement).value,
    ).toBe("sk-ant-bad");
  });

  it("clear fires DELETE on the full path with the field name", async () => {
    await renderPanel();
    fireEvent.click(screen.getByTestId("credential-clear-UW_TOKEN"));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1),
    );
    expect(calls.find((c) => c.method === "DELETE")!.url).toBe(
      "/api/credentials/unusual_whales?name=UW_TOKEN",
    );
  });

  it("a submitted secret never renders back into the DOM after success", async () => {
    await renderPanel();
    fireEvent.change(document.getElementById("cred-ANTHROPIC_API_KEY")!, {
      target: { value: "sk-ant-super-secret" },
    });
    fireEvent.click(screen.getByTestId("credential-save-anthropic"));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1),
    );
    await waitFor(() =>
      expect(screen.getByTestId("credential-notice-anthropic")).toBeTruthy(),
    );
    expect(document.body.innerHTML).not.toContain("sk-ant-super-secret");
  });
});
