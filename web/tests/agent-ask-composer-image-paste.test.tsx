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
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  });

  it("drops an image whose decoded size exceeds 5 MB", async () => {
    const { send, textarea } = renderComposer();
    const oversized = imageFile("huge.png", "image/png", new Uint8Array(5 * 1024 * 1024 + 1));
    await pasteFiles(textarea, [oversized]);

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
    const [text, engine, attachments] = onSubmit.mock.calls[0];
    expect(text).toBe("what is this chain telling me");
    expect(engine).toBe("AUTO");
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
