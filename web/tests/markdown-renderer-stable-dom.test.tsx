// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import MarkdownRenderer from "@/components/MarkdownRenderer";

// iOS drops a text selection when the selected nodes are replaced. A parent
// re-render with unchanged content must reuse the rendered markdown nodes.
describe("MarkdownRenderer DOM stability", () => {
  it("keeps the same paragraph node across a re-render with unchanged content", () => {
    const content = "First paragraph with **bold** text.\n\nSecond paragraph.";
    const { container, rerender } = render(<MarkdownRenderer content={content} preserveWhitespace />);
    const before = container.querySelector(".chat-markdown-p");
    const strongBefore = container.querySelector(".chat-markdown-strong");

    rerender(<MarkdownRenderer content={content} preserveWhitespace />);

    expect(container.querySelector(".chat-markdown-p")).toBe(before);
    expect(container.querySelector(".chat-markdown-strong")).toBe(strongBefore);
  });
});
