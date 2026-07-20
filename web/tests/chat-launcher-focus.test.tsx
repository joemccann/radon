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
    render(<ChatLauncher activeSection="dashboard" />);

    fireEvent.keyDown(document, { key: "j", ctrlKey: true });

    const composer = await screen.findByLabelText("Message Grok assistant");
    await waitFor(() => expect(document.activeElement).toBe(composer));

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Radon chat" })).toBeNull();
  });
});
