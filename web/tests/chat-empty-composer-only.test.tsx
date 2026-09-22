/** @vitest-environment jsdom */
// The assistant opens with useful orientation and editable prompt starters.

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

describe("chat welcome and composer", () => {
  it("names the assistant, current workspace context, and available starting points", () => {
    render(<ChatPanel activeSection="portfolio" />);
    expect(screen.getByRole("heading", { name: "Radon AI" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: /What do you want to understand/ })).toBeTruthy();
    expect(screen.getByText("Positions")).toBeTruthy();
    expect(screen.getByText("Data retrieved when needed")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Review portfolio risk/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Investigate market flow/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Pressure-test a trade/ })).toBeTruthy();
  });

  it("starter selection creates an editable draft before any assistant request", () => {
    render(<ChatPanel activeSection="dashboard" />);
    fireEvent.click(screen.getByRole("button", { name: /Review portfolio risk/ }));
    const input = screen.getByLabelText("Ask Radon") as HTMLTextAreaElement;
    expect(input.value).toContain("Review my current portfolio risk");
    expect(document.activeElement).toBe(input);
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url) === "/api/assistant")).toBe(false);
  });

  it("exposes image attachment and Send actions with real keyboard guidance", () => {
    render(<ChatPanel activeSection="dashboard" />);
    expect(screen.getByRole("button", { name: "Attach images" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send", exact: true })).toBeTruthy();
    expect(screen.getByText("Enter to send · Shift+Enter for a new line")).toBeTruthy();
  });

  it("provides discoverable close and reset controls in the launcher", async () => {
    render(<ChatLauncher activeSection="dashboard" portfolio={null} />);
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    await waitFor(() => expect(screen.getByLabelText("Ask Radon")).toBeTruthy());
    expect(screen.getByRole("button", { name: "New conversation" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
    expect(screen.queryByRole("dialog", { name: "Radon chat" })).toBeNull();
  });
});
