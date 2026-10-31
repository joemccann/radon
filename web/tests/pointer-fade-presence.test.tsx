/** @vitest-environment jsdom */
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ColumnsToggle } from "../components/ColumnsToggle";
import NewsfeedShare from "../components/NewsfeedShare";
import ResearchFeedback from "../components/ResearchFeedback";

vi.mock("@/lib/newsfeedShare", async (original) => ({
  ...await original<typeof import("@/lib/newsfeedShare")>(),
  renderShareCard: vi.fn(() => new Promise(() => {})),
  supportsMp4Export: () => false,
}));

let reduced = false;
const listeners = new Set<() => void>();
beforeEach(() => {
  vi.useFakeTimers();
  reduced = false;
  listeners.clear();
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    get matches() { return reduced; },
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  })));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const subjects = [
  { kind: "feedback", label: "Thumbs up", enter: 160, exit: 120 },
  { kind: "share", label: "Share", enter: 160, exit: 120 },
  { kind: "columns", label: "COLUMNS", enter: 125, exit: 100 },
] as const;

function subject(kind: typeof subjects[number]["kind"]) {
  if (kind === "feedback") return render(<ResearchFeedback postId="sample" onHidden={vi.fn()} />);
  if (kind === "share") return render(<NewsfeedShare post={{ id: "sample", title: "Market data", content: "Sample report.", timestamp: "2026-10-30T12:00:00Z", images: [], tags: [] }} />);
  return render(<ColumnsToggle columns={[{ key: "ticker", label: "Ticker" }]} visible={{ ticker: true }} onToggle={vi.fn()} />);
}

for (const { kind, label, enter, exit } of subjects) {
  describe(`${kind} pointer fade`, () => {
    it("uses the audited opacity timings for a pointer opening", () => {
      const { container } = subject(kind);
      fireEvent.click(screen.getByRole("button", { name: new RegExp(label) }), { detail: 1 });
      const panel = container.querySelector<HTMLElement>('[data-fade-state="open"]')!;
      expect(panel).not.toBeNull();
      expect(panel.dataset.fadeMotion).toBe("pointer");
      expect(panel.style.getPropertyValue("--pointer-fade-enter")).toBe(`${enter}ms`);
      expect(panel.style.getPropertyValue("--pointer-fade-exit")).toBe(`${exit}ms`);
      expect(panel.hasAttribute("inert")).toBe(false);
    });

    it("removes a closing panel from interaction immediately, then unmounts", () => {
      const { container } = subject(kind);
      const trigger = screen.getByRole("button", { name: new RegExp(label) });
      fireEvent.click(trigger, { detail: 1 });
      const panel = container.querySelector<HTMLElement>('[data-fade-state="open"]')!;
      fireEvent.click(trigger, { detail: 1 });
      expect(panel.isConnected).toBe(true);
      expect(panel.dataset.fadeState).toBe("exiting");
      expect(panel.hasAttribute("inert")).toBe(true);
      expect(panel.getAttribute("aria-hidden")).toBe("true");
      act(() => vi.advanceTimersByTime(exit + 50));
      expect(panel.isConnected).toBe(false);
    });

    it("opens and closes immediately for keyboard/assistive clicks", () => {
      const { container } = subject(kind);
      const trigger = screen.getByRole("button", { name: new RegExp(label) });
      fireEvent.click(trigger, { detail: 0 });
      const panel = container.querySelector<HTMLElement>('[data-fade-state="open"]')!;
      expect(panel?.dataset.fadeMotion).toBe("instant");
      fireEvent.click(trigger, { detail: 0 });
      expect(panel.isConnected).toBe(false);
    });

    it("waits for its own zero-opacity transition, not a child or stale entrance", () => {
      const { container } = subject(kind);
      const trigger = screen.getByRole("button", { name: new RegExp(label) });
      fireEvent.click(trigger, { detail: 1 });
      const panel = container.querySelector<HTMLElement>('[data-fade-state="open"]')!;
      fireEvent.click(trigger, { detail: 1 });
      const end = (target: Element, propertyName = "opacity") => {
        const event = new Event("transitionend", { bubbles: true });
        Object.defineProperty(event, "propertyName", { value: propertyName });
        fireEvent(target, event);
      };
      panel.style.opacity = "1";
      end(panel);
      expect(panel.isConnected).toBe(true);
      panel.style.opacity = "0";
      end(panel, "color");
      end(panel.firstElementChild!);
      expect(panel.isConnected).toBe(true);
      end(panel);
      expect(panel.isConnected).toBe(false);
    });

    it("cancels an exit when rapidly reopened and ignores stale transition ends", () => {
      const { container } = subject(kind);
      const trigger = screen.getByRole("button", { name: new RegExp(label) });
      fireEvent.click(trigger, { detail: 1 });
      const panel = container.querySelector<HTMLElement>('[data-fade-state="open"]')!;
      fireEvent.click(trigger, { detail: 1 });
      fireEvent.click(trigger, { detail: 1 });
      fireEvent.transitionEnd(panel, { propertyName: "opacity" });
      act(() => vi.advanceTimersByTime(500));
      expect(container.querySelector('[data-fade-state="open"]')).toBe(panel);
      expect(panel.hasAttribute("inert")).toBe(false);
    });

    it("honors reduced motion and preference changes during an exit", () => {
      const { container } = subject(kind);
      const trigger = screen.getByRole("button", { name: new RegExp(label) });
      reduced = true;
      fireEvent.click(trigger, { detail: 1 });
      expect(container.querySelector<HTMLElement>('[data-fade-state="open"]')?.dataset.fadeMotion).toBe("instant");
      fireEvent.click(trigger, { detail: 1 });
      expect(container.querySelector("[data-fade-state]")).toBeNull();
      reduced = false;
      fireEvent.click(trigger, { detail: 1 });
      fireEvent.click(trigger, { detail: 1 });
      act(() => { reduced = true; listeners.forEach(listener => listener()); });
      expect(container.querySelector("[data-fade-state]")).toBeNull();
    });

    it("dismisses with Escape immediately and returns focus to its trigger", () => {
      const { container } = subject(kind);
      const trigger = screen.getByRole("button", { name: new RegExp(label) });
      fireEvent.click(trigger, { detail: 1 });
      const input = container.querySelector<HTMLInputElement | HTMLTextAreaElement>("textarea, input")!;
      input.focus();
      fireEvent.keyDown(input, { key: "Escape" });
      expect(container.querySelector("[data-fade-state]")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });

    it("cleans up pending exits and the preference listener on unmount", () => {
      const view = subject(kind);
      const trigger = screen.getByRole("button", { name: new RegExp(label) });
      fireEvent.click(trigger, { detail: 1 });
      fireEvent.click(trigger, { detail: 1 });
      expect(vi.getTimerCount()).toBeGreaterThan(0);
      view.unmount();
      // Flush React's queued zero-delay work without advancing the exit timer.
      act(() => vi.advanceTimersByTime(0));
      expect(vi.getTimerCount()).toBe(0);
      expect(listeners.size).toBe(0);
    });
  });
}

it("cannot send feedback through an inert exiting panel", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const { container } = subject("feedback");
  const trigger = screen.getByRole("button", { name: "Thumbs up" });
  fireEvent.click(trigger, { detail: 1 });
  const save = screen.getByRole("button", { name: "Save feedback" });
  fireEvent.click(trigger, { detail: 1 });
  expect(container.querySelector("[inert]")).not.toBeNull();
  fireEvent.click(save);
  expect(fetch).not.toHaveBeenCalled();
});
