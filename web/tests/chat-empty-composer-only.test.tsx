/**
 * @vitest-environment jsdom
 *
 * Composer-only chat surface (2026-08-29, design-lab Variant A): the ⌘J
 * overlay's empty state is the composer alone — no ASK RADON title/copy, no
 * boxed starter-prompt cards, no in-conversation pill row, no launcher header
 * bar. Chrome collapses into the composer rail (chips, model, ESC hint) and
 * Enter sends via a quiet ↵ control instead of an ASK button.
 */

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPanel from "@/components/ChatPanel";
import ChatLauncher from "@/components/ChatLauncher";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("chat empty state is the composer alone", () => {
  it("renders no title, copy, starter cards, or pills", () => {
    const { container } = render(<ChatPanel activeSection="dashboard" />);
    expect(container.querySelector(".chat-empty-state")).toBeNull();
    expect(container.querySelector(".chat-empty-card")).toBeNull();
    expect(container.querySelector(".chat-pills")).toBeNull();
    expect(screen.queryByText("Ask Radon", { selector: "div" })).toBeNull();
    expect(screen.getByLabelText("Ask Radon")).toBeTruthy();
  });

  it("marks the panel empty so the launcher can size to content", () => {
    const { container } = render(<ChatPanel activeSection="dashboard" />);
    const panel = container.querySelector(".chat-panel");
    expect(panel?.getAttribute("data-empty")).toBe("true");
  });

  it("submits with a quiet enter control, not an ASK button", () => {
    render(<ChatPanel activeSection="dashboard" />);
    const send = screen.getByLabelText("Send");
    expect(send.textContent).toBe("↵");
    expect(screen.queryByText("ASK")).toBeNull();
  });

  it("rail carries the ESC affordance; the launcher header bar is gone", async () => {
    render(
      <ChatLauncher activeSection="dashboard" portfolio={{ positions: [] } as never} />,
    );
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    await waitFor(() => expect(screen.getByLabelText("Ask Radon")).toBeTruthy());
    expect(screen.queryByText("Radon Chat")).toBeNull();
    expect(screen.queryByText("Esc to dismiss")).toBeNull();
    expect(screen.getByText("ESC DISMISSES")).toBeTruthy();
  });
});
