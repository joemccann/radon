// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ErrorToast from "@/components/ErrorToast";
import { useDialogChrome } from "@/lib/useDialogChrome";

function Dialog() {
  const { panelRef } = useDialogChrome<HTMLDivElement>({ open: true });
  return <div ref={panelRef} role="dialog" aria-modal="true" tabIndex={-1}>
    <button>First dialog action</button>
    <button>Last dialog action</button>
    <ErrorToast message="Could not save." onRetry={() => {}} />
  </div>;
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("dialog error toast keyboard access", () => {
  it("includes retry and dismiss in the modal focus cycle and restores focus on dismiss", () => {
    vi.spyOn(HTMLElement.prototype, "offsetParent", "get").mockReturnValue(document.body);
    render(<Dialog />);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-owns")).toContain("radon-toast-viewport");
    screen.getByRole("button", { name: "Last dialog action" }).focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Try again" }));
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Dismiss" }));
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "First dialog action" }));
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Dismiss" }));
    fireEvent.click(document.activeElement!);
    expect(document.activeElement).toBe(dialog);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
