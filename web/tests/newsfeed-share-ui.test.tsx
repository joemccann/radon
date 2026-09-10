/** @vitest-environment jsdom */
import React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import NewsfeedShare from "@/components/NewsfeedShare";

const engine = vi.hoisted(() => ({ buildShareCaption: vi.fn(), renderShareCard: vi.fn(), canvasToPng: vi.fn(), canvasToMp4: vi.fn(), supportsMp4Export: vi.fn() }));
vi.mock("@/lib/newsfeedShare", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/newsfeedShare")>(), ...engine }));
const post = { id: "fixture", title: "Yen hedge demand", content: "Hedge demand increased.", timestamp: "2026-09-07T16:00:00Z", isoTimestamp: "2026-09-07T16:00:00Z", href: "https://example.com/source", images: ["/chart-1.png", "/chart-2.png"], tags: ["JPY"] };
let createUrl: ReturnType<typeof vi.fn>;
let revokeUrl: ReturnType<typeof vi.fn>;
let clipboard: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  vi.resetAllMocks();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ title: post.title, content: post.content, caption: `${post.title}\n\n${post.content}` }) }));
  const actual = await vi.importActual<typeof import("@/lib/newsfeedShare")>("@/lib/newsfeedShare");
  engine.buildShareCaption.mockImplementation(actual.buildShareCaption);
  engine.renderShareCard.mockResolvedValue(document.createElement("canvas"));
  engine.canvasToPng.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
  engine.canvasToMp4.mockResolvedValue(new Blob(["mp4"], { type: "video/mp4" }));
  engine.supportsMp4Export.mockReturnValue(true);
  createUrl = vi.fn().mockReturnValue("blob:share-card");
  revokeUrl = vi.fn();
  clipboard = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createUrl });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeUrl });
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: clipboard } });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function openShare() {
  fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
  await screen.findByAltText("Portrait share preview: Yen hedge demand");
}

describe("news feed sharing", () => {
  it("uses the voice draft for captions and image/video rendering", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ title: "Hedge demand is back.", content: "Positioning is neutral. Source: ZeroHedge" }) } as Response);
    render(<NewsfeedShare post={post} />);
    await openShare();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Hedge demand is back.\n\nPositioning is neutral.");
    expect(engine.renderShareCard).toHaveBeenCalledWith(expect.objectContaining({ title: "Hedge demand is back.", content: "Positioning is neutral." }), undefined);
    expect(fetch).toHaveBeenCalledWith("/api/newsfeed/share", expect.objectContaining({ method: "POST", cache: "no-store" }));
  });

  it.each([
    { imageUrl: "/chart-1.png", provider: "Ramp", otherProvider: "Goldman Sachs" },
    { imageUrl: "/chart-2.png", provider: "Goldman Sachs", otherProvider: "Ramp" },
  ])("preserves $provider in copied and X captions when the rewrite omits the selected image source", async ({ imageUrl, provider, otherProvider }) => {
    const attributed = {
      ...post,
      imageSources: { "/chart-1.png": "Ramp", "/chart-2.png": "Goldman Sachs" },
    };
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ title: "Hedge demand is back.", content: "Positioning remains neutral." }),
    } as Response);
    render(<NewsfeedShare post={attributed} imageUrl={imageUrl} />);
    await openShare();
    const caption = (screen.getByRole("textbox", { name: "Post caption" }) as HTMLTextAreaElement).value;
    expect(caption).toContain("Hedge demand is back.");
    expect(caption).toContain("Positioning remains neutral.");
    expect(caption).toContain(`Source: ${provider}`);
    expect(caption).not.toContain(otherProvider);
    fireEvent.click(screen.getByRole("button", { name: "Copy caption" }));
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith(caption));
    const compose = screen.getByRole("link", { name: "Compose on X" });
    expect(new URL(compose.getAttribute("href")!).searchParams.get("text")).toBe(caption);
    expect(engine.renderShareCard).toHaveBeenLastCalledWith(
      expect.objectContaining({ imageSources: attributed.imageSources }), imageUrl,
    );
  });

  it("keeps Compose on X available while rewriting and aborts on close", async () => {
    vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
    render(<NewsfeedShare post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Copy caption" }) as HTMLButtonElement).disabled).toBe(true);
    const compose = screen.getByRole("link", { name: "Compose on X" });
    expect(new URL(compose.getAttribute("href")!).searchParams.get("text")).toBe("Yen hedge demand\n\nHedge demand increased.");
    expect(compose.getAttribute("aria-disabled")).not.toBe("true");
    expect(compose.getAttribute("target")).toBe("_blank");
    expect((screen.getByRole("button", { name: "Download Story image" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Download Reels / TikTok video" }) as HTMLButtonElement).disabled).toBe(true);
    expect(engine.renderShareCard).not.toHaveBeenCalled();
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const signal = vi.mocked(fetch).mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    expect(signal?.aborted).toBe(true);
  });

  it("updates the X intent after rewriting without waiting for the preview", async () => {
    let resolveRewrite!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(() => new Promise<Response>(resolve => { resolveRewrite = resolve; }));
    engine.renderShareCard.mockImplementation(() => new Promise(() => {}));
    render(<NewsfeedShare post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    const compose = screen.getByRole("link", { name: "Compose on X" });
    expect(new URL(compose.getAttribute("href")!).searchParams.get("text")).toContain(post.title);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await act(async () => {
      resolveRewrite({ ok: true, json: async () => ({ title: "Hedge demand is back.", content: "Positioning remains neutral." }) } as Response);
    });
    await waitFor(() => expect(engine.renderShareCard).toHaveBeenCalledOnce());
    expect(new URL(compose.getAttribute("href")!).searchParams.get("text")).toBe("Hedge demand is back.\n\nPositioning remains neutral.");
    expect(compose.getAttribute("aria-disabled")).not.toBe("true");
    expect(screen.getByText("Preparing preview…")).not.toBeNull();
    expect((screen.getByRole("button", { name: "Download Story image" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows original copy on failure and retries voice generation", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
    render(<NewsfeedShare post={post} />);
    await openShare();
    expect(screen.getByRole("alert").textContent).toContain("Showing the original copy");
    fireEvent.click(screen.getByRole("button", { name: "Retry voice rewrite" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    await screen.findByAltText("Portrait share preview: Yen hedge demand");
  });

  it("prepares only on demand and uses the selected source chart", async () => {
    render(<NewsfeedShare post={post} imageUrl="/chart-2.png" />);
    expect(engine.renderShareCard).not.toHaveBeenCalled();
    await openShare();
    expect(engine.renderShareCard).toHaveBeenCalledWith(post, "/chart-2.png");
    expect(screen.getByRole("button", { name: "Share", exact: true }).getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    expect(screen.queryByRole("region", { name: "Share news item" })).toBeNull();
    expect(revokeUrl).toHaveBeenCalledWith("blob:share-card");
  });

  it("copies edited caption and composes the same encoded text on X", async () => {
    render(<NewsfeedShare post={post} />);
    await openShare();
    const caption = "JPY & USD: hedge demand? #JPY";
    fireEvent.change(screen.getByRole("textbox", { name: "Post caption" }), { target: { value: caption } });
    fireEvent.click(screen.getByRole("button", { name: "Copy caption" }));
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith(caption));
    const link = screen.getByRole("link", { name: "Compose on X" });
    expect(new URL(link.getAttribute("href")!).searchParams.get("text")).toBe(caption);
    expect(link.getAttribute("rel")).toContain("noopener");
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Caption copied."));
  });

  it("downloads the prepared PNG and MP4 with correct filenames", async () => {
    const filenames: string[] = [];
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(function(this: HTMLAnchorElement) { filenames.push(this.download); });
    render(<NewsfeedShare post={post} />);
    await openShare();
    fireEvent.click(screen.getByRole("button", { name: "Download Story image" }));
    await waitFor(() => expect(filenames).toContain("radon-yen-hedge-demand.png"));
    fireEvent.click(screen.getByRole("button", { name: "Download Reels / TikTok video" }));
    await waitFor(() => expect(filenames).toContain("radon-yen-hedge-demand.mp4"));
    expect(engine.canvasToMp4).toHaveBeenCalledWith(expect.any(HTMLCanvasElement), expect.any(AbortSignal));
  });

  it("reports unavailable MP4 support while keeping PNG export available", async () => {
    engine.supportsMp4Export.mockReturnValue(false);
    render(<NewsfeedShare post={post} />);
    await openShare();
    expect((screen.getByRole("button", { name: "Download Reels / TikTok video" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Download Story image" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText(/MP4 export is unavailable/)).not.toBeNull();
  });

  it("retries failed chart loading without discarding caption edits", async () => {
    engine.renderShareCard.mockRejectedValueOnce(new Error("Chart could not be loaded"));
    render(<NewsfeedShare post={post} imageUrl="/chart-2.png" />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    expect((await screen.findByRole("alert")).textContent).toContain("Chart could not be loaded");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "My edited caption" } });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByAltText("Portrait share preview: Yen hedge demand");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("My edited caption");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows clipboard and video failures for recovery", async () => {
    clipboard.mockRejectedValue(new Error("denied"));
    engine.canvasToMp4.mockRejectedValue(new Error("Encoder unavailable"));
    render(<NewsfeedShare post={post} />);
    await openShare();
    fireEvent.click(screen.getByRole("button", { name: "Copy caption" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Select and copy"));
    fireEvent.click(screen.getByRole("button", { name: "Download Reels / TikTok video" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Encoder unavailable"));
    expect((screen.getByRole("button", { name: "Download Story image" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("isolates caption arrows and Escape from lightbox navigation", async () => {
    const parentKeys = vi.fn();
    render(<div onKeyDown={parentKeys}><NewsfeedShare post={post} /></div>);
    await openShare();
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowRight" });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowLeft" });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(parentKeys).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Share", exact: true }));
  });

  it("aborts active recording when the share panel closes", async () => {
    engine.canvasToMp4.mockImplementation(() => new Promise(() => {}));
    render(<NewsfeedShare post={post} />);
    await openShare();
    fireEvent.click(screen.getByRole("button", { name: "Download Reels / TikTok video" }));
    await waitFor(() => expect(engine.canvasToMp4).toHaveBeenCalledOnce());
    const signal = engine.canvasToMp4.mock.calls[0][1] as AbortSignal;
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    expect(signal.aborted).toBe(true);
  });

  it("preserves an edited caption through refreshed post objects and chart selection", async () => {
    const { rerender } = render(<NewsfeedShare post={post} imageUrl="/chart-1.png" />);
    await openShare();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "My carefully edited caption" } });
    rerender(<NewsfeedShare post={{ ...post }} imageUrl="/chart-1.png" />);
    await screen.findByAltText("Portrait share preview: Yen hedge demand");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("My carefully edited caption");
    rerender(<NewsfeedShare post={{ ...post }} imageUrl="/chart-2.png" />);
    await screen.findByAltText("Portrait share preview: Yen hedge demand");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("My carefully edited caption");
    expect(fetch).toHaveBeenCalledOnce();
    expect(engine.renderShareCard).toHaveBeenLastCalledWith(post, "/chart-2.png");
  });

  it("disables exports while the newly selected chart is rendering", async () => {
    const { rerender } = render(<NewsfeedShare post={post} imageUrl="/chart-1.png" />);
    await openShare();
    let resolveChart!: (canvas: HTMLCanvasElement) => void;
    const pendingChart = new Promise<HTMLCanvasElement>(resolve => { resolveChart = resolve; });
    engine.renderShareCard.mockReturnValueOnce(pendingChart);
    rerender(<NewsfeedShare post={post} imageUrl="/chart-2.png" />);
    await waitFor(() => expect(engine.renderShareCard).toHaveBeenLastCalledWith(post, "/chart-2.png"));
    expect(screen.queryByAltText("Portrait share preview: Yen hedge demand")).toBeNull();
    expect((screen.getByRole("button", { name: "Download Story image" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Download Reels / TikTok video" }) as HTMLButtonElement).disabled).toBe(true);
    const newCanvas = document.createElement("canvas");
    await act(async () => { resolveChart(newCanvas); await pendingChart; });
    await screen.findByAltText("Portrait share preview: Yen hedge demand");
    expect((screen.getByRole("button", { name: "Download Story image" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Download Reels / TikTok video" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Download Story image" }));
    await waitFor(() => expect(engine.canvasToPng).toHaveBeenLastCalledWith(newCanvas));
  });

});
