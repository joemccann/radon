/**
 * @vitest-environment jsdom
 *
 * R-624 (P2, NF-10): the per-turn error copy repeats forever with no dwell
 * bound and no counter — turn 1 and turn 200 of a sustained provider outage
 * look identical, and the error rail is deliberately cleared for non-PI
 * turns. After N consecutive failed turns the operator must be told the
 * assistant is degraded, not handed the same sentence again.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ChatPanel from "@/components/ChatPanel";

afterEach(cleanup);

async function failingTurn(container: HTMLElement, text: string) {
  const textarea = screen.getByLabelText("Ask Radon");
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.submit(textarea.closest("form")!);
  await waitFor(() => {
    expect(container.querySelector(".engine-trace")).toBeNull();
  }, { timeout: 4000 });
}

describe("ChatPanel degraded indicator", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // @ts-expect-error test stub
    global.fetch = vi.fn(async () => {
      throw new Error("provider overloaded");
    });
  });

  it("shows no degraded chip after a single failure", async () => {
    const { container } = render(<ChatPanel activeSection="portfolio" />);
    await failingTurn(container, "one");
    expect(container.querySelector(".chat-degraded")).toBeNull();
  });

  it("shows the degraded chip once failures are consecutive", async () => {
    const { container } = render(<ChatPanel activeSection="portfolio" />);
    await failingTurn(container, "one");
    await failingTurn(container, "two");
    await failingTurn(container, "three");
    await waitFor(() => {
      expect(container.querySelector(".chat-degraded")).not.toBeNull();
    });
  });

  it("clears the chip after a turn succeeds", async () => {
    const { container } = render(<ChatPanel activeSection="portfolio" />);
    await failingTurn(container, "one");
    await failingTurn(container, "two");
    await failingTurn(container, "three");
    await waitFor(() => {
      expect(container.querySelector(".chat-degraded")).not.toBeNull();
    });

    // @ts-expect-error test stub
    global.fetch = vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({ content: "back" }),
    } as Response));
    await failingTurn(container, "four");
    await waitFor(() => {
      expect(container.querySelector(".chat-degraded")).toBeNull();
    });
  });
});
