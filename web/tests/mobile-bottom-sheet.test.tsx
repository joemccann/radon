/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import BottomSheet from "../components/mobile/BottomSheet";

describe("BottomSheet", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <BottomSheet open={false} onClose={() => {}} title="Title">
        <p>body</p>
      </BottomSheet>,
    );
    expect(container.firstChild).toBeNull();
    cleanup();
  });

  it("renders title, body, and close button when open", () => {
    render(
      <BottomSheet open onClose={() => {}} title="Hello" testId="sheet">
        <p>body content</p>
      </BottomSheet>,
    );

    expect(screen.getByTestId("sheet")).toBeTruthy();
    expect(screen.getByText("Hello")).toBeTruthy();
    expect(screen.getByText("body content")).toBeTruthy();
    expect(screen.getByTestId("sheet-close")).toBeTruthy();
    cleanup();
  });

  it("invokes onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(
      <BottomSheet open onClose={onClose} title="t" testId="sheet">
        <p>body</p>
      </BottomSheet>,
    );

    fireEvent.click(screen.getByTestId("sheet-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it("invokes onClose when the backdrop is clicked", () => {
    const onClose = vi.fn();
    render(
      <BottomSheet open onClose={onClose} title="t" testId="sheet">
        <p>body</p>
      </BottomSheet>,
    );

    const backdrop = document.querySelector(".mobile-sheet-backdrop");
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop!);
    expect(onClose).toHaveBeenCalledTimes(1);
    cleanup();
  });

  // iPhone cutoff regression, part 2 (2026-07-21): the sheet rendered inline
  // inside its caller's subtree. Inside the ticker asset-deck (a stacking
  // context at z-index 40) the sheet's own z-index 90 was capped at level 40,
  // so the mobile tab bar (z-index 60) painted OVER the sheet footer — the
  // Review / BUY / SELL buttons were buried under the tab bar. The sheet MUST
  // portal to document.body (the NewsfeedLightbox pattern) so no ancestor
  // stacking context can ever cap it.
  it("portals the sheet root to document.body", () => {
    render(
      <div style={{ zIndex: 40, position: "absolute" }}>
        <BottomSheet open onClose={() => {}} title="t" testId="sheet">
          <p>body</p>
        </BottomSheet>
      </div>,
    );

    expect(screen.getByTestId("sheet").parentElement).toBe(document.body);
    cleanup();
  });

  it("invokes onClose when Escape is pressed", () => {
    const onClose = vi.fn();
    render(
      <BottomSheet open onClose={onClose} title="t" testId="sheet">
        <p>body</p>
      </BottomSheet>,
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it("renders a footer when provided", () => {
    render(
      <BottomSheet open onClose={() => {}} title="t" footer={<button>submit</button>} testId="sheet">
        <p>body</p>
      </BottomSheet>,
    );

    expect(screen.getByText("submit")).toBeTruthy();
    cleanup();
  });

  // iOS Safari sizes `vh` against the LARGE viewport (toolbar collapsed), so an
  // inline `max-height` in vh pushes the sticky footer below the visible area.
  // The maxHeight prop must flow through the --sheet-max-h custom property so
  // the .m-sheet rule can clamp it against the real visible height.
  it("expresses maxHeight through --sheet-max-h instead of an inline max-height", () => {
    render(
      <BottomSheet open onClose={() => {}} title="t" testId="sheet" maxHeight="82dvh">
        <p>body</p>
      </BottomSheet>,
    );

    const panel = screen.getByTestId("sheet-panel");
    expect(panel.style.getPropertyValue("--sheet-max-h")).toBe("82dvh");
    expect(panel.style.maxHeight).toBe("");
    cleanup();
  });

  // The iOS keyboard shrinks only the VISUAL viewport; the fixed sheet root
  // stays full-height, leaving the footer (Review / Confirm & send) buried
  // behind the keyboard with no way to scroll it into view. The sheet must
  // measure the keyboard overlap from visualViewport and lift itself above it.
  it("tracks the keyboard overlap from visualViewport into --keyboard-inset", () => {
    const viewport = new EventTarget() as EventTarget & { height: number; offsetTop: number };
    viewport.height = 800;
    viewport.offsetTop = 0;
    Object.defineProperty(window, "visualViewport", { value: viewport, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 800, configurable: true, writable: true });

    render(
      <BottomSheet open onClose={() => {}} title="t" testId="sheet">
        <p>body</p>
      </BottomSheet>,
    );

    const root = screen.getByTestId("sheet");
    expect(root.style.getPropertyValue("--keyboard-inset")).toBe("0px");

    viewport.height = 500; // keyboard opened: 300px of overlap
    viewport.dispatchEvent(new Event("resize"));
    expect(root.style.getPropertyValue("--keyboard-inset")).toBe("300px");

    viewport.height = 800; // keyboard dismissed
    viewport.dispatchEvent(new Event("resize"));
    expect(root.style.getPropertyValue("--keyboard-inset")).toBe("0px");

    Object.defineProperty(window, "visualViewport", { value: undefined, configurable: true });
    cleanup();
  });

  it("captures the drag handle and releases it after dismissing", () => {
    const onClose = vi.fn();
    render(
      <BottomSheet open onClose={onClose} title="t" testId="sheet">
        <p>body</p>
      </BottomSheet>,
    );
    const handle = screen.getByRole("separator", { name: "Drag to dismiss" }) as HTMLDivElement & {
      setPointerCapture: ReturnType<typeof vi.fn>;
      releasePointerCapture: ReturnType<typeof vi.fn>;
      hasPointerCapture: ReturnType<typeof vi.fn>;
    };
    handle.setPointerCapture = vi.fn();
    handle.releasePointerCapture = vi.fn();
    handle.hasPointerCapture = vi.fn(() => true);

    fireEvent.pointerDown(handle, { pointerId: 7, clientY: 0 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientY: 160 });
    fireEvent.pointerUp(handle, { pointerId: 7, clientY: 160 });

    expect(handle.setPointerCapture).toHaveBeenCalledWith(7);
    expect(handle.releasePointerCapture).toHaveBeenCalledWith(7);
    expect(onClose).toHaveBeenCalledTimes(1);
    cleanup();
  });
});
