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
    const { container } = render(
      <BottomSheet open onClose={onClose} title="t" testId="sheet">
        <p>body</p>
      </BottomSheet>,
    );

    const backdrop = container.querySelector(".mobile-sheet-backdrop");
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop!);
    expect(onClose).toHaveBeenCalledTimes(1);
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
});
