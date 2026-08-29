// @vitest-environment jsdom
//
// AskComposer — the chat composer that replaces ChatPanel's inline textarea/form.
//
// The submit semantics it inherits from ChatPanel are the whole point of this
// spec: Enter sends, Shift+Enter inserts a newline, and an in-flight IME
// composition suppresses BOTH (a CJK/emoji candidate-window Enter commits the
// candidate — sending there fires a half-typed prompt at the assistant).
//
// focusKey replaces ChatPanel's composerRef focus effect: it focuses on mount and
// on every change, so the ⌘J overlay still lands the caret in the composer.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import AskComposer from "../components/agent/AskComposer";

function renderComposer(overrides: Partial<React.ComponentProps<typeof AskComposer>> = {}) {
  const onSubmit = vi.fn();
  const utils = render(<AskComposer onSubmit={onSubmit} {...overrides} />);
  const textarea = screen.getByLabelText("Ask Radon") as HTMLTextAreaElement;
  return { ...utils, onSubmit, textarea };
}

function type(textarea: HTMLTextAreaElement, value: string) {
  fireEvent.change(textarea, { target: { value } });
}

afterEach(() => {
  cleanup();
});

describe("AskComposer — Enter to send", () => {
  it("submits the trimmed text on bare Enter", () => {
    const { onSubmit, textarea } = renderComposer();
    type(textarea, "  scan MU flow  ");
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toBe("scan MU flow");
  });

  it("passes the selected engine as the second argument (AUTO by default)", () => {
    const { onSubmit, textarea } = renderComposer();
    type(textarea, "risk check");
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSubmit.mock.calls[0][1]).toBe("AUTO");
  });

  it("clears the textarea after a send", () => {
    const { textarea } = renderComposer();
    type(textarea, "scan MU flow");
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(textarea.value).toBe("");
  });

  it("does not send whitespace-only input", () => {
    const { onSubmit, textarea } = renderComposer();
    type(textarea, "   ");
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not send while busy", () => {
    const { onSubmit, textarea } = renderComposer({ busy: true });
    type(textarea, "scan MU flow");
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits on the send button click", () => {
    const { onSubmit, textarea } = renderComposer();
    type(textarea, "scan MU flow");
    fireEvent.click(screen.getByLabelText("Send"));

    expect(onSubmit).toHaveBeenCalledWith("scan MU flow", "AUTO", []);
  });

  it("disables the send button on empty input and enables it once typed", () => {
    const { textarea } = renderComposer();
    const send = screen.getByLabelText("Send") as HTMLButtonElement;
    expect(send.disabled).toBe(true);

    type(textarea, "scan MU flow");
    expect(send.disabled).toBe(false);
  });
});

describe("AskComposer — Shift+Enter inserts a newline", () => {
  it("does NOT submit when Shift is held", () => {
    const { onSubmit, textarea } = renderComposer();
    type(textarea, "line one");
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("leaves the draft intact so the newline survives", () => {
    const { textarea } = renderComposer();
    type(textarea, "line one");
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(textarea.value).toBe("line one");
  });
});

describe("AskComposer — IME composition guard", () => {
  it("does NOT submit on Enter while a composition session is open", () => {
    const { onSubmit, textarea } = renderComposer();
    type(textarea, "ミュー");
    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does NOT submit when the native event reports isComposing", () => {
    const { onSubmit, textarea } = renderComposer();
    type(textarea, "ミュー");
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits again once the composition has ended", () => {
    const { onSubmit, textarea } = renderComposer();
    type(textarea, "ミュー");
    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.compositionEnd(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("ミュー", "AUTO", []);
  });
});

describe("AskComposer — focusKey autofocus", () => {
  it("focuses the textarea on mount", () => {
    const { textarea } = renderComposer({ focusKey: true });
    expect(document.activeElement).toBe(textarea);
  });

  it("re-focuses when focusKey changes", () => {
    const onSubmit = vi.fn();
    const { rerender } = render(<AskComposer onSubmit={onSubmit} focusKey={1} />);
    const textarea = screen.getByLabelText("Ask Radon") as HTMLTextAreaElement;

    textarea.blur();
    expect(document.activeElement).not.toBe(textarea);

    rerender(<AskComposer onSubmit={onSubmit} focusKey={2} />);
    expect(document.activeElement).toBe(textarea);
  });

  it("does not steal focus when focusKey is false (panel closed)", () => {
    const { textarea } = renderComposer({ focusKey: false });
    expect(document.activeElement).not.toBe(textarea);
  });
});
