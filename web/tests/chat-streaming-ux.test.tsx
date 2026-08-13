/**
 * @vitest-environment jsdom
 *
 * Streaming-UX regressions for the chat overlay rebuild:
 *   - MarkdownRenderer renders NOTHING for empty content (no "No output." flash).
 *   - An in-flight assistant turn shows the typing indicator, never "No output."
 *     or the old hard-coded "Analyzing flow, structure, and risk context...".
 *   - The empty / first-run state offers clickable prompt cards.
 *   - A completed assistant message exposes a Copy action.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ChatPanel from "@/components/ChatPanel";
import MarkdownRenderer from "@/components/MarkdownRenderer";

afterEach(cleanup);

describe("MarkdownRenderer empty handling", () => {
  it("renders nothing (never 'No output.') for empty content", () => {
    const { container } = render(<MarkdownRenderer content="" />);
    expect(container.textContent).toBe("");
    expect(container.querySelector(".chat-empty")).toBeNull();
  });
});

describe("ChatPanel streaming UX", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the first-run prompt cards before any message is sent", () => {
    render(<ChatPanel activeSection="portfolio" />);
    expect(screen.getByText("Ask Radon")).toBeTruthy();
    const buttons = screen.getAllByRole("button");
    expect(buttons.some((b) => /portfolio/i.test(b.textContent ?? ""))).toBe(true);
  });

  // The pending affordance is now <EngineTrace> (a real step trace fed by the
  // loop's tool telemetry) rather than the three typing dots; the invariant
  // under test is unchanged — a pending turn shows progress, never "No output."
  it("renders an engine trace (not 'No output.') while a turn is in flight", async () => {
    let resolveTurn: (value: unknown) => void = () => {};
    const pending = new Promise((resolve) => {
      resolveTurn = resolve;
    });
    const fetchMock = vi.fn(async () => {
      const body = await pending;
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    const { container } = render(<ChatPanel activeSection="portfolio" />);
    const textarea = screen.getByLabelText("Ask Radon");
    fireEvent.change(textarea, { target: { value: "hello there" } });
    fireEvent.submit(textarea.closest("form")!);

    await waitFor(() => {
      expect(container.querySelector(".engine-trace")).not.toBeNull();
    });
    expect(screen.queryByText("No output.")).toBeNull();
    expect(screen.queryByText(/Analyzing flow, structure/i)).toBeNull();

    resolveTurn({ content: "Here is the analysis." });

    await waitFor(
      () => {
        expect(container.querySelector(".engine-trace")).toBeNull();
      },
      { timeout: 4000 },
    );
  });

  it("exposes a Copy action on a completed assistant message", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/api/pi")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: "ok", output: "Portfolio loaded.", command: "portfolio" }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({ content: "ok" }) } as Response;
    });
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    render(<ChatPanel activeSection="portfolio" />);
    const textarea = screen.getByLabelText("Ask Radon");
    fireEvent.change(textarea, { target: { value: "/portfolio" } });
    fireEvent.submit(textarea.closest("form")!);

    await waitFor(
      () => {
        expect(screen.getByRole("button", { name: /copy message/i })).toBeTruthy();
      },
      { timeout: 4000 },
    );
  });
});
