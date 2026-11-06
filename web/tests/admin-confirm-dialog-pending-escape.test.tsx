/**
 * @vitest-environment jsdom
 *
 * Admin ConfirmDialog: while the confirmed action is in flight (`pending`),
 * Escape must be inert, exactly like the backdrop and the Cancel button.
 * Otherwise owners clear their confirm state mid-flight and the operator
 * loses the only surface reporting the outcome.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ConfirmDialog from "../components/admin/ConfirmDialog";

afterEach(cleanup);

describe("ConfirmDialog pending gate", () => {
  it("ignores Escape while pending", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog open pending title="Stop API?" body="Stops the API." confirmLabel="Stop" onConfirm={() => {}} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByTestId("admin-confirm"));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("still cancels on Escape when not pending", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog open title="Stop API?" body="Stops the API." confirmLabel="Stop" onConfirm={() => {}} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
