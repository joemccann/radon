// @vitest-environment jsdom
//
// AskComposer — clipboard image attachments.
//
// An operator screenshots a chain, a vol surface, or a broker error and pastes
// it straight into the composer. The bytes have to reach onSubmit as RAW base64
// (no "data:" prefix) because the wire format is the Anthropic image block
// verbatim — a prefixed payload is rejected by the API, silently costing the
// operator the image their prompt was written about.
//
// Everything here asserts on the ARGUMENT PASSED TO onSubmit, never on internal
// state: the third positional argument IS the contract with ChatPanel.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import AskComposer from "../components/agent/AskComposer";

/** 0x89 0x50 0x4E 0x47 — the PNG magic number. base64: "iVBORw==". */
const PNG_BYTES = new Uint8Array([137, 80, 78, 71]);
const PNG_BASE64 = "iVBORw==";

function imageFile(name: string, type: string, bytes: Uint8Array = PNG_BYTES) {
  return new File([bytes], name, { type });
}

/** A clipboard carrying `items`, the shape Chrome/Safari hand a paste handler. */
function clipboardWithFiles(files: File[]) {
  return {
    items: files.map((file) => ({
      kind: "file" as const,
      type: file.type,
      getAsFile: () => file,
    })),
    files,
  };
}

function renderComposer(overrides: Partial<React.ComponentProps<typeof AskComposer>> = {}) {
  const onSubmit = vi.fn();
  const utils = render(<AskComposer onSubmit={onSubmit} {...overrides} />);
  const textarea = screen.getByLabelText("Ask Radon") as HTMLTextAreaElement;
  const send = screen.getByLabelText("Send") as HTMLButtonElement;
  return { ...utils, onSubmit, textarea, send };
}

async function pasteFiles(textarea: HTMLTextAreaElement, files: File[]) {
  fireEvent.paste(textarea, { clipboardData: clipboardWithFiles(files) });
  // FileReader is async even for a 4-byte blob.
  await waitFor(() => expect(true).toBe(true));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AskComposer — pasting an image", () => {
  it("adds a thumbnail and enables send with an empty textarea", async () => {
    const { send, textarea } = renderComposer();
    expect(send.disabled).toBe(true);

    await pasteFiles(textarea, [imageFile("chart.png", "image/png")]);

    await waitFor(() => {
      expect(screen.getAllByRole("img")).toHaveLength(1);
    });
    expect(textarea.value).toBe("");
    expect(send.disabled).toBe(false);
  });

  it("renders the thumbnail from a data URL built at render time", async () => {
    const { textarea } = renderComposer();
    await pasteFiles(textarea, [imageFile("chart.png", "image/png")]);

    const img = await screen.findByRole("img");
    expect(img.getAttribute("src")).toBe(`data:image/png;base64,${PNG_BASE64}`);
  });

  it("ignores a text/plain clipboard entry", async () => {
    const { send, textarea } = renderComposer();
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [{ kind: "string", type: "text/plain", getAsFile: () => null }],
        files: [],
      },
    });
    await waitFor(() => expect(true).toBe(true));

    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(send.disabled).toBe(true);
  });

  it("rejects a disallowed image type (image/svg+xml)", async () => {
    const { send, textarea } = renderComposer();
    await pasteFiles(textarea, [imageFile("payload.svg", "image/svg+xml")]);
    expect(screen.getByRole("alert").textContent).toContain("use a PNG, JPEG, GIF, or WebP image");

    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(send.disabled).toBe(true);
  });

  it("caps the attachment list at 4", async () => {
    const { textarea } = renderComposer();
    await pasteFiles(
      textarea,
      ["a", "b", "c", "d", "e"].map((n) => imageFile(`${n}.png`, "image/png")),
    );

    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(4));
    expect(screen.getByRole("alert").textContent).toContain("attach up to 4 images");
  });

  it("explains why an image larger than 5 MB was rejected", async () => {
    const { send, textarea } = renderComposer();
    const oversized = imageFile("huge.png", "image/png", new Uint8Array(5 * 1024 * 1024 + 1));
    await pasteFiles(textarea, [oversized]);
    expect(screen.getByRole("alert").textContent).toContain("images must be 5 MB or smaller");

    await waitFor(() => {
      expect(screen.queryAllByRole("img")).toHaveLength(0);
    });
    expect(send.disabled).toBe(true);
  });
});

describe("AskComposer — submitting attachments", () => {
  it("passes the attachment array as the THIRD onSubmit argument, raw base64", async () => {
    const { onSubmit, textarea } = renderComposer();
    await pasteFiles(textarea, [imageFile("chart.png", "image/png")]);
    await screen.findByRole("img");

    fireEvent.change(textarea, { target: { value: "what is this chain telling me" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const [text, modelId, attachments] = onSubmit.mock.calls[0];
    expect(text).toBe("what is this chain telling me");
    expect(modelId).toBe("");
    expect(attachments).toHaveLength(1);
    expect(attachments[0]).toMatchObject({
      mediaType: "image/png",
      data: PNG_BASE64,
      name: "chart.png",
    });
    expect(attachments[0].data.startsWith("data:")).toBe(false);
    expect(typeof attachments[0].id).toBe("string");
    expect(attachments[0].id.length).toBeGreaterThan(0);
    expect(attachments[0]).not.toHaveProperty("dataUrl");
  });

  it("sends an image-only turn with empty text", async () => {
    const { onSubmit, textarea } = renderComposer();
    await pasteFiles(textarea, [imageFile("chart.png", "image/png")]);
    await screen.findByRole("img");

    fireEvent.click(screen.getByLabelText("Send"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toBe("");
    expect(onSubmit.mock.calls[0][2]).toHaveLength(1);
  });

  it("clears the attachments after a successful submit", async () => {
    const { onSubmit, textarea } = renderComposer();
    await pasteFiles(textarea, [imageFile("chart.png", "image/png")]);
    await screen.findByRole("img");

    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(screen.queryAllByRole("img")).toHaveLength(0));

    fireEvent.change(textarea, { target: { value: "follow up" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit.mock.calls[1][2]).toEqual([]);
  });

  it("does not submit an empty turn with no attachments", () => {
    const { onSubmit, textarea } = renderComposer();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("AskComposer — removing an attachment", () => {
  it("drops only the attachment whose remove control was clicked", async () => {
    const { onSubmit, textarea } = renderComposer();
    await pasteFiles(textarea, [
      imageFile("first.png", "image/png"),
      imageFile("second.png", "image/png"),
    ]);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));

    const removes = screen.getAllByLabelText("Remove image");
    expect(removes).toHaveLength(2);
    fireEvent.click(removes[0]);

    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(1));

    fireEvent.click(screen.getByLabelText("Send"));
    const attachments = onSubmit.mock.calls[0][2];
    expect(attachments).toHaveLength(1);
    expect(attachments[0].name).toBe("second.png");
  });

  it("disables send again once the last attachment is removed", async () => {
    const { send, textarea } = renderComposer();
    await pasteFiles(textarea, [imageFile("chart.png", "image/png")]);
    await screen.findByRole("img");
    expect(send.disabled).toBe(false);

    fireEvent.click(screen.getByLabelText("Remove image"));

    await waitFor(() => expect(send.disabled).toBe(true));
  });
});

describe("AskComposer — paste does not disturb existing composer behavior", () => {
  it("leaves a text paste to the browser (no preventDefault)", () => {
    const { textarea } = renderComposer();
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", {
      value: { items: [{ kind: "string", type: "text/plain", getAsFile: () => null }], files: [] },
    });
    fireEvent(textarea, event);

    expect(event.defaultPrevented).toBe(false);
  });

  it("still suppresses Enter during an IME composition when an image is attached", async () => {
    const { onSubmit, textarea } = renderComposer();
    await pasteFiles(textarea, [imageFile("chart.png", "image/png")]);
    await screen.findByRole("img");

    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.compositionEnd(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});


describe("AskComposer — picker and pending image reads", () => {
  function deferReads() {
    const readers: { result: string | null; onload: null | (() => void); onerror: null | (() => void) }[] = [];
    class DeferredReader {
      result: string | null = null;
      onload: null | (() => void) = null;
      onerror: null | (() => void) = null;
      readAsDataURL() { readers.push(this); }
    }
    vi.stubGlobal("FileReader", DeferredReader);
    return readers;
  }

  it("attaches chosen files through the visible picker action", async () => {
    const { onSubmit } = renderComposer();
    const picker = screen.getByLabelText("Choose images") as HTMLInputElement;
    const click = vi.spyOn(picker, "click");
    fireEvent.click(screen.getByRole("button", { name: "Attach images" }));
    expect(click).toHaveBeenCalledTimes(1);
    fireEvent.change(picker, { target: { files: [imageFile("picked.png", "image/png")] } });
    await screen.findByRole("img", { name: "picked.png" });
    fireEvent.click(screen.getByLabelText("Send"));
    expect(onSubmit.mock.calls[0][2][0]).toMatchObject({ name: "picked.png", data: PNG_BASE64 });
  });

  it("blocks Enter and Send until pending image reads finish", async () => {
    const readers = deferReads();
    const { onSubmit, textarea, send } = renderComposer();
    fireEvent.change(textarea, { target: { value: "Read this chart" } });
    fireEvent.paste(textarea, { clipboardData: clipboardWithFiles([imageFile("chart.png", "image/png")]) });
    expect(send.disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("Preparing images");
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    await act(async () => {
      readers[0].result = `data:image/png;base64,${PNG_BASE64}`;
      readers[0].onload?.();
    });
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    expect(onSubmit.mock.calls[0][2]).toHaveLength(1);
  });

  it("does not leak an old pending image into a replaced draft", async () => {
    const readers = deferReads();
    const { onSubmit, textarea, rerender } = renderComposer();
    fireEvent.paste(textarea, { clipboardData: clipboardWithFiles([imageFile("old.png", "image/png")]) });
    const restored = { id: "restored", mediaType: "image/png" as const, data: PNG_BASE64, name: "restored.png" };
    rerender(<AskComposer onSubmit={onSubmit} draft={{ id: 1, text: "Try again", attachments: [restored] }} />);
    await act(async () => {
      readers[0].result = `data:image/png;base64,${PNG_BASE64}`;
      readers[0].onload?.();
    });
    expect(screen.queryByRole("img", { name: "old.png" })).toBeNull();
    expect(screen.getByRole("img", { name: "restored.png" })).toBeTruthy();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("Try again", "", [restored]);
  });

  it("reserves image slots across overlapping pastes", async () => {
    const readers = deferReads();
    const { textarea } = renderComposer();
    fireEvent.paste(textarea, { clipboardData: clipboardWithFiles(["a", "b", "c"].map((name) => imageFile(`${name}.png`, "image/png"))) });
    fireEvent.paste(textarea, { clipboardData: clipboardWithFiles(["d", "e"].map((name) => imageFile(`${name}.png`, "image/png"))) });
    expect(readers).toHaveLength(4);
    expect(screen.getByRole("alert").textContent).toContain("e.png: attach up to 4 images");
    await act(async () => {
      for (const reader of readers) {
        reader.result = `data:image/png;base64,${PNG_BASE64}`;
        reader.onload?.();
      }
    });
    expect(screen.getAllByRole("img")).toHaveLength(4);
  });

  it("reports a read failure and releases the pending send lock", async () => {
    const readers = deferReads();
    const { textarea, send } = renderComposer();
    fireEvent.change(textarea, { target: { value: "Read chart" } });
    fireEvent.paste(textarea, { clipboardData: clipboardWithFiles([imageFile("broken.png", "image/png")]) });
    await act(async () => { readers[0].onerror?.(); });
    expect(screen.getByRole("alert").textContent).toContain("broken.png: could not attach");
    expect(send.disabled).toBe(false);
  });
});
