/**
 * @vitest-environment jsdom
 *
 * Client transport for pasted chat images. The gate here is the WIRE: a pasted
 * image must leave the browser as an Anthropic image block on the latest user
 * message of POST /api/assistant, and a turn with no attachment must keep the
 * plain-string content shape it has always had.
 *
 * ChatPanel owns the fetch, so ChatPanel is what renders. AskComposer is
 * replaced with a submit harness: the clipboard-to-attachment capture is a
 * separate contract, and this file pins the transport, not the paste.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ChatImageAttachment } from "@/lib/types";

const PASTED: ChatImageAttachment = {
  id: "0-screenshot.png",
  mediaType: "image/png",
  data: "iVBORw0KGgoAAAANSUhEUg==",
  name: "screenshot.png",
};

vi.mock("@/components/agent", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/agent")>();
  return {
    ...actual,
    AskComposer: ({
      onSubmit,
    }: {
      onSubmit: (text: string, engine: string, attachments: ChatImageAttachment[]) => void;
    }) =>
      React.createElement(
        "div",
        null,
        React.createElement(
          "button",
          {
            type: "button",
            "data-testid": "submit-with-image",
            onClick: () => onSubmit("read this chart", "AUTO", [PASTED]),
          },
          "with image",
        ),
        React.createElement(
          "button",
          {
            type: "button",
            "data-testid": "submit-plain",
            onClick: () => onSubmit("read this chart", "AUTO", []),
          },
          "plain",
        ),
        React.createElement(
          "button",
          {
            type: "button",
            "data-testid": "submit-image-only",
            onClick: () => onSubmit("", "AUTO", [PASTED]),
          },
          "image only",
        ),
      ),
  };
});

import ChatPanel from "../components/ChatPanel";

type SentRequest = { url: string; method: string; body: string };
const sent: SentRequest[] = [];
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  sent.length = 0;
  fetchMock.mockReset();
  fetchMock.mockImplementation((input, init) => {
    sent.push({
      url: String(input),
      method: (init as RequestInit | undefined)?.method ?? "GET",
      body: String((init as RequestInit | undefined)?.body ?? ""),
    });
    return Promise.resolve(
      new Response(JSON.stringify({ content: "Read." }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function lastMessageOf(request: SentRequest) {
  const parsed = JSON.parse(request.body) as {
    messages: Array<{ role: string; content: unknown }>;
  };
  return parsed.messages[parsed.messages.length - 1];
}

describe("pasted chat image reaches the assistant endpoint", () => {
  it("sends the image as an Anthropic image block ahead of the text block", async () => {
    render(<ChatPanel activeSection="orders" />);

    expect(sent).toHaveLength(0);
    fireEvent.click(screen.getByTestId("submit-with-image"));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].url).toBe("/api/assistant");
    expect(sent[0].method).toBe("POST");

    const latest = lastMessageOf(sent[0]);
    expect(latest.role).toBe("user");
    expect(latest.content).toEqual([
      {
        type: "image",
        source: { type: "base64", media_type: "image/png", data: "iVBORw0KGgoAAAANSUhEUg==" },
      },
      { type: "text", text: "read this chart" },
    ]);
  });

  it("sends an image-only turn with no text", async () => {
    render(<ChatPanel activeSection="orders" />);

    fireEvent.click(screen.getByTestId("submit-image-only"));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].url).toBe("/api/assistant");
    expect(sent[0].method).toBe("POST");

    const latest = lastMessageOf(sent[0]);
    expect(latest.role).toBe("user");
    // No empty text block rides along: the image is the whole message.
    expect(latest.content).toEqual([
      {
        type: "image",
        source: { type: "base64", media_type: "image/png", data: "iVBORw0KGgoAAAANSUhEUg==" },
      },
    ]);
  });

  it("renders the image-only turn in the operator's own bubble", async () => {
    const { container } = render(<ChatPanel activeSection="orders" />);

    fireEvent.click(screen.getByTestId("submit-image-only"));

    const thumb = await waitFor(() => {
      const found = container.querySelector<HTMLImageElement>(".chat-message.user img");
      expect(found).not.toBeNull();
      return found as HTMLImageElement;
    });
    expect(thumb.getAttribute("src")).toBe("data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==");
  });

  it("keeps plain-string content when nothing is attached", async () => {
    render(<ChatPanel activeSection="orders" />);
    fireEvent.click(screen.getByTestId("submit-plain"));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].url).toBe("/api/assistant");
    expect(lastMessageOf(sent[0]).content).toBe("read this chart");
  });

  it("renders the pasted image in the operator's own bubble", async () => {
    const { container } = render(<ChatPanel activeSection="orders" />);
    fireEvent.click(screen.getByTestId("submit-with-image"));

    await waitFor(() => expect(sent).toHaveLength(1));
    const thumb = await waitFor(() => {
      const found = container.querySelector<HTMLImageElement>(".chat-message.user img");
      expect(found).not.toBeNull();
      return found as HTMLImageElement;
    });
    expect(thumb.getAttribute("src")).toBe("data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==");
  });
});
