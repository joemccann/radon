// @vitest-environment jsdom
//
// Model picker — the composer's rail select is fed by GET /api/models, and the
// id it holds has to reach the assistant endpoint.
//
// The old ENGINES list was decoration: five invented engine names whose value
// ChatPanel dropped on the floor. The replacement is only worth anything if the
// selected id lands in the POST body, so every assertion here is at the WIRE —
// full path, method, and the parsed body — with a paired assertion that nothing
// was sent before the operator submitted.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import AskComposer from "../components/agent/AskComposer";
import ChatPanel from "../components/ChatPanel";

const CATALOG = {
  models: [
    {
      id: "claude-opus-5",
      provider: "anthropic",
      label: "CLAUDE OPUS 5",
      refreshedAt: "2026-08-29",
    },
    { id: "gpt-5.5", provider: "openai", label: "GPT", refreshedAt: "2026-08-29" },
    { id: "grok-4.6", provider: "xai", label: "GROK", refreshedAt: "2026-08-29" },
  ],
  defaultId: "claude-opus-5",
  source: "turso",
};

type SentRequest = { url: string; method: string; body: string };

const sent: SentRequest[] = [];
const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function record(input: RequestInfo | URL, init?: RequestInit) {
  const url = String(input);
  sent.push({ url, method: init?.method ?? "GET", body: String(init?.body ?? "") });
  return url;
}

/** Serves the catalog on GET /api/models and a canned turn on the assistant. */
function serveCatalog(catalog: unknown = CATALOG) {
  fetchMock.mockImplementation((input, init) => {
    const url = record(input as RequestInfo | URL, init as RequestInit | undefined);
    if (url === "/api/models") return Promise.resolve(jsonResponse(catalog));
    return Promise.resolve(jsonResponse({ content: "Read." }));
  });
}

function assistantRequests() {
  return sent.filter((request) => request.url === "/api/assistant");
}

function bodyOf(request: SentRequest) {
  return JSON.parse(request.body) as {
    messages: Array<{ role: string; content: unknown }>;
    model?: string;
  };
}

function picker() {
  return screen.getByLabelText("Model") as HTMLSelectElement;
}

function optionLabels() {
  return Array.from(picker().options).map((option) => option.textContent);
}

/** A clipboard carrying `items`, the shape a browser hands a paste handler. */
function clipboardWithFiles(files: File[]) {
  return {
    items: files.map((file) => ({
      kind: "file" as const,
      type: file.type,
      getAsFile: () => file,
    })),
    files,
  };
}

beforeEach(() => {
  sent.length = 0;
  fetchMock.mockReset();
  serveCatalog();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AskComposer — the picker is derived, not hardcoded", () => {
  it("populates its options from GET /api/models", async () => {
    render(<AskComposer onSubmit={vi.fn()} />);

    await waitFor(() => expect(optionLabels()).toEqual(["CLAUDE OPUS 5", "GPT", "GROK"]));
    expect(sent[0].url).toBe("/api/models");
    expect(sent[0].method).toBe("GET");
    expect(picker().value).toBe("claude-opus-5");
  });

  it("shows one entry when the deployment has one provider key", async () => {
    serveCatalog({
      models: [CATALOG.models[0]],
      defaultId: "claude-opus-5",
      source: "builtin",
    });
    render(<AskComposer onSubmit={vi.fn()} />);

    await waitFor(() => expect(optionLabels()).toEqual(["CLAUDE OPUS 5"]));
  });

  it("keeps a usable picker and a working submit when the catalog fetch fails", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error("offline")));
    const onSubmit = vi.fn();
    render(<AskComposer onSubmit={onSubmit} />);

    const textarea = screen.getByLabelText("Ask Radon") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "still works" } });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    // No catalog means no id to name: the turn goes out on the server default.
    expect(onSubmit.mock.calls[0][0]).toBe("still works");
    expect(onSubmit.mock.calls[0][1]).toBe("");
    expect(onSubmit.mock.calls[0][2]).toEqual([]);
    expect(picker()).toBeTruthy();
  });

  it("passes the selected model id as the second onSubmit argument", async () => {
    const onSubmit = vi.fn();
    render(<AskComposer onSubmit={onSubmit} />);
    await waitFor(() => expect(optionLabels()).toHaveLength(3));

    fireEvent.change(picker(), { target: { value: "grok-4.6" } });
    fireEvent.change(screen.getByLabelText("Ask Radon"), { target: { value: "flow on MU" } });
    fireEvent.click(screen.getByLabelText("Send"));

    expect(onSubmit).toHaveBeenCalledWith("flow on MU", "grok-4.6", []);
  });

  it("still sends on Enter and still inserts a newline on Shift+Enter", async () => {
    const onSubmit = vi.fn();
    render(<AskComposer onSubmit={onSubmit} />);
    await waitFor(() => expect(optionLabels()).toHaveLength(3));

    const textarea = screen.getByLabelText("Ask Radon");
    fireEvent.change(textarea, { target: { value: "enter sends" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("enter sends", "claude-opus-5", []);

    fireEvent.change(textarea, { target: { value: "shift enter" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("does not send while an IME composition is open", async () => {
    const onSubmit = vi.fn();
    render(<AskComposer onSubmit={onSubmit} />);
    await waitFor(() => expect(optionLabels()).toHaveLength(3));

    const textarea = screen.getByLabelText("Ask Radon");
    fireEvent.change(textarea, { target: { value: "にほんご" } });
    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.compositionEnd(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("にほんご", "claude-opus-5", []);
  });
});

describe("ChatPanel — the selected model reaches POST /api/assistant", () => {
  it("carries the picked model id in the request body", async () => {
    render(<ChatPanel activeSection="orders" />);
    await waitFor(() => expect(optionLabels()).toHaveLength(3));

    // Paired negative: the gate is closed until the operator submits.
    expect(assistantRequests()).toHaveLength(0);

    fireEvent.change(picker(), { target: { value: "grok-4.6" } });
    fireEvent.change(screen.getByLabelText("Ask Radon"), { target: { value: "size this" } });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => expect(assistantRequests()).toHaveLength(1));
    const request = assistantRequests()[0];
    expect(request.url).toBe("/api/assistant");
    expect(request.method).toBe("POST");
    expect(bodyOf(request).model).toBe("grok-4.6");
  });

  it("sends the catalog default when the picker is never touched", async () => {
    render(<ChatPanel activeSection="orders" />);
    await waitFor(() => expect(optionLabels()).toHaveLength(3));

    fireEvent.change(screen.getByLabelText("Ask Radon"), { target: { value: "size this" } });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => expect(assistantRequests()).toHaveLength(1));
    expect(bodyOf(assistantRequests()[0]).model).toBe("claude-opus-5");
  });

  it("omits model entirely when no catalog answered", async () => {
    fetchMock.mockImplementation((input, init) => {
      const url = record(input as RequestInfo | URL, init as RequestInit | undefined);
      if (url === "/api/models") return Promise.reject(new Error("offline"));
      return Promise.resolve(jsonResponse({ content: "Read." }));
    });
    render(<ChatPanel activeSection="orders" />);

    fireEvent.change(screen.getByLabelText("Ask Radon"), { target: { value: "size this" } });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => expect(assistantRequests()).toHaveLength(1));
    expect(bodyOf(assistantRequests()[0])).not.toHaveProperty("model");
  });

  // Starter-prompt pills/cards removed 2026-08-29 (composer-only surface); the
  // picked-model-reaches-the-wire contract is held by the typed-send tests above.

  it("still carries a pasted image alongside the model id", async () => {
    render(<ChatPanel activeSection="orders" />);
    await waitFor(() => expect(optionLabels()).toHaveLength(3));

    const textarea = screen.getByLabelText("Ask Radon") as HTMLTextAreaElement;
    fireEvent.paste(textarea, {
      clipboardData: clipboardWithFiles([
        new File([new Uint8Array([137, 80, 78, 71])], "chart.png", { type: "image/png" }),
      ]),
    });
    await screen.findByRole("img");

    fireEvent.change(picker(), { target: { value: "gpt-5.5" } });
    fireEvent.change(textarea, { target: { value: "read this chart" } });
    fireEvent.click(screen.getByLabelText("Send"));

    await waitFor(() => expect(assistantRequests()).toHaveLength(1));
    const body = bodyOf(assistantRequests()[0]);
    expect(body.model).toBe("gpt-5.5");
    const latest = body.messages[body.messages.length - 1];
    expect(latest.content).toEqual([
      {
        type: "image",
        source: { type: "base64", media_type: "image/png", data: "iVBORw==" },
      },
      { type: "text", text: "read this chart" },
    ]);
  });
});
