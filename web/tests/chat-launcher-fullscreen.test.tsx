/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import ChatLauncher from "@/components/ChatLauncher";

afterEach(cleanup);

describe("ChatLauncher full screen", () => {
  it("toggles the chat dialog between windowed and full screen", async () => {
    render(<ChatLauncher activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });

    const expand = await screen.findByRole("button", { name: "Enter full screen" }, { timeout: 5000 });
    const dialog = screen.getByRole("dialog", { name: "Radon chat" });
    expect(dialog.dataset.expanded).toBeUndefined();
    expect(expand.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(expand);
    expect(dialog.dataset.expanded).toBe("true");
    const collapse = screen.getByRole("button", { name: "Exit full screen" });
    expect(collapse.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(collapse);
    expect(dialog.dataset.expanded).toBeUndefined();
    expect(screen.getByRole("button", { name: "Enter full screen" })).toBeTruthy();
  });

  it("keeps full screen across close and reopen", async () => {
    render(<ChatLauncher activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    fireEvent.click(await screen.findByRole("button", { name: "Enter full screen" }, { timeout: 5000 }));

    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });

    expect(screen.getByRole("dialog", { name: "Radon chat" }).dataset.expanded).toBe("true");
  });
});
