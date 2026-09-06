/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import ChatLauncher from "@/components/ChatLauncher";

afterEach(cleanup);

describe("ChatLauncher", () => {
  it("moves focus to the composer when the Radon Chat modal opens", async () => {
    render(<ChatLauncher activeSection="dashboard" portfolio={{ positions: [] } as never} />);

    fireEvent.keyDown(document, { key: "j", ctrlKey: true });

    // Autofocus now comes from AskComposer's `focusKey` (fed the launcher's
    // open flag), not ChatPanel's removed composerRef effect.
    // The first open now downloads the assistant module; wait for its actual
    // composer instead of assuming that module was part of the initial page.
    const composer = await screen.findByLabelText("Ask Radon", {}, { timeout: 5000 });
    await waitFor(() => expect(document.activeElement).toBe(composer));

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Radon chat" })).toBeNull();
  });
});
