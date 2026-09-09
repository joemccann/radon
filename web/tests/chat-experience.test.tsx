// @vitest-environment jsdom
// Exercise the real composer, panel and transport; only token animation is accelerated.
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiMessage, Message } from "@/lib/types";

vi.mock("@/lib/chat", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/chat")>();
  return {
    ...actual,
    streamMessage: async (
      id: string,
      content: string,
      setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
      options: { signal?: AbortSignal } = {},
    ) => {
      if (options.signal?.aborted) return;
      setMessages((messages) => messages.map((message) => message.id === id ? { ...message, content } : message));
    },
  };
});

import ChatPanel from "@/components/ChatPanel";
import ChatLauncher from "@/components/ChatLauncher";

const requests: { messages: ApiMessage[]; model?: string; signal: AbortSignal | null | undefined }[] = [];
let responses: Array<Response | Promise<Response>> = [];
const fetchMock = vi.fn<typeof fetch>();

function json(content: string, extra = {}) {
  return new Response(JSON.stringify({ content, ...extra }), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  requests.length = 0;
  responses = [];
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input, init) => {
    if (String(input) === "/api/models") {
      return new Response(JSON.stringify({
        models: [{ id: "model-one", label: "Model One" }, { id: "model-two", label: "Model Two" }],
        defaultId: "model-one",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (String(input) !== "/api/assistant") throw new Error(`Unexpected request: ${String(input)}`);
    requests.push({ ...JSON.parse(String(init?.body)), signal: init?.signal });
    return await (responses.shift() ?? json("Analysis complete."));
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function ready() {
  await waitFor(() => expect((screen.getByLabelText("Model") as HTMLSelectElement).value).toBe("model-one"));
}

function send(text: string) {
  fireEvent.change(screen.getByLabelText("Ask Radon"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send", exact: true }));
}

async function completed(content: string) {
  await screen.findByText(content);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Stop response" })).toBeNull());
}

describe("Radon chat response lifecycle", () => {
  it("aborts the fetch, accepts a replacement turn, and ignores a late order proposal", async () => {
    let resolveOld!: (response: Response) => void;
    responses.push(new Promise<Response>((resolve) => { resolveOld = resolve; }), json("Replacement analysis."));
    render(<ChatPanel activeSection="portfolio" />);
    await ready();
    send("Compare downside scenarios");
    await waitFor(() => expect(requests).toHaveLength(1));
    const oldSignal = requests[0].signal;
    expect(oldSignal).toBeInstanceOf(AbortSignal);
    expect(oldSignal?.aborted).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Stop response" }));
    expect(oldSignal?.aborted).toBe(true);
    expect(screen.getByText("Response stopped.")).toBeTruthy();

    send("Explain the replacement thesis");
    await completed("Replacement analysis.");
    expect(requests).toHaveLength(2);
    expect(requests[1].signal?.aborted).toBe(false);
    expect(requests[1].messages.some((message) => message.content === "Response stopped.")).toBe(false);

    await act(async () => {
      resolveOld(json("Late order proposal", { proposal: {
        tool: "place_order", destructive: true, toolUseId: "late-tool", summary: "Buy late stock",
        input: { type: "stock", ticker: "SPY", action: "BUY", quantity: 1, limit_price: 1 },
      } }));
    });
    expect(screen.queryByText("Late order proposal")).toBeNull();
    expect(screen.queryByText("Confirmation required")).toBeNull();
    expect(screen.getByText("Replacement analysis.")).toBeTruthy();
    expect(fetchMock.mock.calls.every(([url]) => String(url) !== "/api/orders/place")).toBe(true);
  });

  it.each(["HTTP", "SSE"])("retries a failed %s turn with its original history and the current model", async (failure) => {
    const failed = failure === "HTTP"
      ? new Response(JSON.stringify({ error: "Provider unavailable" }), { status: 503 })
      : new Response('event: start\ndata: {}\n\nevent: error\ndata: {"error":"provider diagnostic"}\n\n', {
        status: 200, headers: { "Content-Type": "text/event-stream" },
      });
    responses.push(json("First answer."), failed, json("Recovered answer."));
    render(<ChatPanel activeSection="portfolio" />);
    await ready();
    send("Explain the thesis");
    await completed("First answer.");
    send("Explain the counterevidence");
    const retry = await screen.findByRole("button", { name: "Try again" });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "model-two" } });
    fireEvent.click(retry);
    await completed("Recovered answer.");

    expect(requests).toHaveLength(3);
    expect(requests[2].model).toBe("model-two");
    expect(requests[2].messages).toEqual([
      { role: "user", content: "Explain the thesis" },
      { role: "assistant", content: "First answer." },
      { role: "user", content: "Explain the counterevidence" },
    ]);
    expect(screen.getAllByTestId("chat-message-user")).toHaveLength(2);
    expect(screen.queryByText(/couldn't complete this turn/)).toBeNull();
  });

  it("editing an earlier prompt replaces its conversation branch", async () => {
    responses.push(json("First answer."), json("Old second answer."), json("Old third answer."), json("Revised answer."));
    render(<ChatPanel activeSection="portfolio" />);
    await ready();
    send("Explain the thesis");
    await completed("First answer.");
    send("Old second question");
    await completed("Old second answer.");
    send("Old third question");
    await completed("Old third answer.");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit prompt" })[1]);
    expect((screen.getByLabelText("Ask Radon") as HTMLTextAreaElement).value).toBe("Old second question");
    expect(screen.getByText("Editing previous prompt")).toBeTruthy();
    send("Revised second question");
    await completed("Revised answer.");
    expect(requests[3].messages).toEqual([
      { role: "user", content: "Explain the thesis" },
      { role: "assistant", content: "First answer." },
      { role: "user", content: "Revised second question" },
    ]);
    expect(screen.queryByText("Old second answer.")).toBeNull();
    expect(screen.queryByText("Old third question")).toBeNull();
    expect(screen.queryByText("Editing previous prompt")).toBeNull();
  });

  it("retains image content in history when a later turn refers to it", async () => {
    responses.push(json("Image analysis."), json("Follow-up analysis."));
    render(<ChatPanel activeSection="portfolio" />);
    await ready();
    fireEvent.change(screen.getByLabelText("Choose images"), { target: { files: [
      new File([new Uint8Array([137, 80, 78, 71])], "chart.png", { type: "image/png" }),
    ] } });
    await screen.findByRole("img", { name: "chart.png" });
    send("Read this chart");
    await completed("Image analysis.");
    send("What contradicts that chart?");
    await completed("Follow-up analysis.");
    expect(requests[1].messages[0]).toEqual({
      role: "user",
      content: [
        { type: "image", source: { type: "base64", media_type: "image/png", data: "iVBORw==" } },
        { type: "text", text: "Read this chart" },
      ],
    });
    expect(requests[1].messages.at(-1)).toEqual({ role: "user", content: "What contradicts that chart?" });
  });
});

describe("Radon chat session continuity", () => {
  it("retains draft and transcript across dismissal, then explicitly resets a new conversation", async () => {
    responses.push(json("Saved session answer."), json("New session answer."));
    render(<><button type="button">Workspace control</button><ChatLauncher activeSection="portfolio" portfolio={null} /></>);
    const opener = screen.getByRole("button", { name: "Workspace control" });
    opener.focus();
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    await screen.findByLabelText("Ask Radon", {}, { timeout: 5000 });
    await ready();
    fireEvent.change(screen.getByLabelText("Ask Radon"), { target: { value: "Keep this draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
    expect(screen.queryByRole("dialog", { name: "Radon chat" })).toBeNull();
    expect(document.activeElement).toBe(opener);

    fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    expect((screen.getByLabelText("Ask Radon") as HTMLTextAreaElement).value).toBe("Keep this draft");
    send("Keep this conversation");
    await completed("Saved session answer.");
    fireEvent.change(screen.getByLabelText("Ask Radon"), { target: { value: "Unsent follow-up" } });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Radon chat" })).toBeNull();
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "Radon chat" });
    expect(within(dialog).getByText("Saved session answer.")).toBeTruthy();
    expect((within(dialog).getByLabelText("Ask Radon") as HTMLTextAreaElement).value).toBe("Unsent follow-up");

    fireEvent.click(within(dialog).getByRole("button", { name: "New conversation" }));
    expect(screen.queryByText("Saved session answer.")).toBeNull();
    expect((screen.getByLabelText("Ask Radon") as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByRole("heading", { name: /What do you want to understand/ })).toBeTruthy();
    send("Start over with a clean question");
    await completed("New session answer.");
    expect(requests[1].messages).toEqual([{ role: "user", content: "Start over with a clean question" }]);
  });
});
