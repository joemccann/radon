// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ErrorToast from "@/components/ErrorToast";
import ChatLauncher from "@/components/ChatLauncher";

vi.mock("next/dynamic", () => ({ default: () => function ChatPanel() { return <button>Chat action</button>; } }));
afterEach(cleanup);

describe("chat modal toast recovery", () => {
  it("keeps an existing toast interactive and in the modal keyboard cycle", () => {
    const retry = vi.fn();
    render(<><ErrorToast message="Refresh failed." onRetry={retry} /><ChatLauncher activeSection="dashboard" portfolio={null} /></>);
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "Radon chat" });
    const viewport = document.getElementById("radon-toast-viewport")!;
    expect(viewport.inert).not.toBe(true);
    expect(dialog.getAttribute("aria-owns")).toBe(viewport.id);
    screen.getByRole("button", { name: "Chat action" }).focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Try again" }));
    fireEvent.click(document.activeElement!);
    expect(retry).toHaveBeenCalledOnce();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Dismiss" }));
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Chat action" }));
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(document.activeElement).toBe(dialog);
  });
});
