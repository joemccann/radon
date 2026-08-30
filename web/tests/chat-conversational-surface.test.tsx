/**
 * @vitest-environment jsdom
 *
 * Conversational-surface restyle (2026-07-24): the Radon Chat overlay read as
 * boxes-within-boxes — the launcher panel border, a duplicate ChatPanel
 * section-header, a bordered transcript box, and bordered composer parts.
 * These pins hold the single-surface contract (one header, one bordered
 * element: the composer container).
 */
import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import ChatPanel from "../components/ChatPanel";

vi.mock("@ai-sdk/react", async () => {
  const actual = await vi.importActual<object>("@ai-sdk/react");
  return actual;
});

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function ruleBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} rule missing`).toBeGreaterThan(-1);
  return css.slice(start, css.indexOf("}", start));
}

describe("Radon Chat conversational surface", () => {
  it("ChatPanel renders no duplicate section-header (launcher head is the only header)", () => {
    const { container } = render(<ChatPanel activeSection="orders" />);
    expect(container.querySelector(".section-header")).toBeNull();
    expect(container.querySelector(".section")).toBeNull();
    cleanup();
  });

  it("transcript is open canvas — no inner border box", () => {
    expect(ruleBlock(".chat-messages")).not.toMatch(/border: 1px solid/);
  });

  it("messages separate by spacing, not dashed rules", () => {
    expect(ruleBlock(".chat-message")).not.toMatch(/border-bottom/);
  });

  it("composer is the single bordered container; textarea and send are borderless inside", () => {
    const row = ruleBlock(".chat-input-row");
    expect(row).toMatch(/border: 1px solid/);
    expect(ruleBlock(".chat-textarea")).toMatch(/border: 0|border: none/);
    expect(ruleBlock(".chat-send")).not.toMatch(/border: 1px solid/);
  });
});
