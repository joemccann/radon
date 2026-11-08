/** @vitest-environment jsdom */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import NewsfeedPdfExport from "@/components/NewsfeedPdfExport";

const engine = vi.hoisted(() => ({ buildArticlePdf: vi.fn() }));
vi.mock("@/lib/newsfeedPdf", () => engine);

const post = { id: "dc", title: "Data-center power", content: "Financing tightens.", timestamp: "2026-11-07T15:00:00Z",
  isoTimestamp: "2026-11-07T15:00:00Z", href: "https://example.com/dc", images: ["/a.png", "/b.png"] };
const pdf = new File(["%PDF-1.3"], "radon-data-center-power.pdf", { type: "application/pdf" });
let share: ReturnType<typeof vi.fn>;
let click: ReturnType<typeof vi.spyOn>;

function asPhone(coarse: boolean) {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: (query: string) => ({ matches: coarse && query === "(pointer: coarse)" }) });
}

beforeEach(() => {
  engine.buildArticlePdf.mockResolvedValue({ file: pdf, missingFigures: 0 });
  share = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "share", { configurable: true, value: share });
  Object.defineProperty(navigator, "canShare", { configurable: true, value: vi.fn().mockReturnValue(true) });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:pdf") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); engine.buildArticlePdf.mockReset(); });

describe("news article PDF export", () => {
  it("builds nothing until the button is pressed", () => {
    render(<NewsfeedPdfExport post={post} />);
    expect(engine.buildArticlePdf).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Export PDF" })).toBeTruthy();
  });

  it("downloads the PDF by name on desktop", async () => {
    asPhone(false);
    render(<NewsfeedPdfExport post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    await screen.findByText("PDF downloaded.");
    expect(engine.buildArticlePdf).toHaveBeenCalledWith(post);
    expect(share).not.toHaveBeenCalled();
    const anchor = click.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("radon-data-center-power.pdf");
    expect(anchor.href).toBe("blob:pdf");
  });

  it("hands the PDF file to the phone share sheet so it can be saved to Files", async () => {
    asPhone(true);
    render(<NewsfeedPdfExport post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    await screen.findByText("PDF shared.");
    expect(share).toHaveBeenCalledTimes(1);
    const [{ files, title }] = share.mock.calls[0];
    expect(files).toEqual([pdf]);
    expect(files[0].type).toBe("application/pdf");
    expect(title).toBe("radon-data-center-power.pdf");
    expect(click).not.toHaveBeenCalled();
  });

  it("asks for a second tap when the installed app loses the gesture, then shares without rebuilding", async () => {
    asPhone(true);
    share.mockRejectedValueOnce(new DOMException("gesture", "NotAllowedError"));
    render(<NewsfeedPdfExport post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    await screen.findByText("PDF ready. Tap Save PDF to save it to Files.");
    fireEvent.click(screen.getByRole("button", { name: "Save PDF" }));
    await screen.findByText("PDF shared.");
    expect(share).toHaveBeenCalledTimes(2);
    expect(engine.buildArticlePdf).toHaveBeenCalledTimes(1);
  });

  it("reports charts that could not be included", async () => {
    asPhone(false);
    engine.buildArticlePdf.mockResolvedValue({ file: pdf, missingFigures: 1 });
    render(<NewsfeedPdfExport post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    await screen.findByText("PDF downloaded. 1 chart could not be loaded and is marked in the PDF.");
  });

  it("shows a retryable error when the build fails", async () => {
    asPhone(false);
    engine.buildArticlePdf.mockRejectedValueOnce(new Error("boom"));
    render(<NewsfeedPdfExport post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("PDF downloaded.");
  });
});
