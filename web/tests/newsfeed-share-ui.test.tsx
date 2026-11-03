/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import NewsfeedShare from "@/components/NewsfeedShare";

const engine = vi.hoisted(() => ({ buildShareCaption: vi.fn(), renderShareCard: vi.fn(), canvasToPng: vi.fn(), canvasToMp4: vi.fn(), supportsMp4Export: vi.fn() }));
vi.mock("@/lib/newsfeedShare", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/newsfeedShare")>(), ...engine }));
const post = { id: "fixture", title: "Yen hedge demand", content: "Hedge demand increased.", timestamp: "2026-09-07T16:00:00Z", isoTimestamp: "2026-09-07T16:00:00Z", href: "https://example.com/source", images: ["/chart-1.png", "/chart-2.png"], tags: ["JPY"] };
const equityIssuance = {
  id: "equity-issuance-252bn",
  title: "US corporates raised a record $252bn in 2Q; Goldman estimates ~$700bn total equity supply in 2026, significant portion AI-related",
  content: "US corporates raised a record $252bn in 2Q across IPOs, follow-ons, convertibles, and SPACs. The desk estimates total corporate equity supply will reach ~$700bn in 2026. Hyperscaler capex is a big slice. Supply overhang is a headwind, not a gale.",
  timestamp: "2026-09-20T20:06:00Z",
  isoTimestamp: "2026-09-20T20:06:00Z",
  href: "https://example.com/equity-issuance",
  images: ["/chart-1.png"],
  tags: ["EQUITY-ISSUANCE"],
  source: { kind: "dropbox" as const, publisher: "Goldman Midday Market Intelligence", documentDate: "2026-09-17", folderDate: "2026-09-20", pages: [1], figures: [], fileId: "id", revision: "r", contentHash: "h", url: "/private.pdf" },
};
let createUrl: ReturnType<typeof vi.fn>;
let revokeUrl: ReturnType<typeof vi.fn>;
let clipboard: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  vi.resetAllMocks();
  vi.stubGlobal("fetch", vi.fn());
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
  await waitFor(() => expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(false));
}

describe("news feed sharing", () => {

  it("immediately enables original copy while the media preview is pending", async () => {
    engine.renderShareCard.mockImplementation(() => new Promise(() => {}));
    render(<NewsfeedShare post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    const caption = screen.getByRole("textbox", { name: "Post caption" }) as HTMLTextAreaElement;
    expect(caption.value).toBe("Yen hedge demand\n\nHedge demand increased.");
    expect(caption.disabled).toBe(false);
    const compose = screen.getByRole("link", { name: "Compose on X" });
    expect(new URL(compose.getAttribute("href")!).searchParams.get("text")).toBe(caption.value);
    expect(compose.getAttribute("aria-disabled")).not.toBe("true");
    expect((screen.getByRole("button", { name: "Copy caption" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Copy caption" }));
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith(caption.value));
    expect((screen.getByRole("button", { name: "Download Story image" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Download Reels / TikTok video" }) as HTMLButtonElement).disabled).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("resets the caption and preview when rapidly reopened during its exit", async () => {
    render(<NewsfeedShare post={post} />);
    await openShare();
    fireEvent.change(screen.getByRole("textbox", { name: "Post caption" }), { target: { value: "Edited fixture" } });
    const trigger = screen.getByRole("button", { name: "Share", exact: true });
    fireEvent.click(trigger, { detail: 1 });
    expect(document.querySelector('[data-fade-state="exiting"][inert]')).not.toBeNull();
    fireEvent.click(trigger, { detail: 1 });
    expect((screen.getByRole("textbox", { name: "Post caption" }) as HTMLTextAreaElement).value).toBe(engine.buildShareCaption(post));
    await waitFor(() => expect(engine.renderShareCard).toHaveBeenCalledTimes(2));
  });

  it("ignores clipboard completion from a previous opening", async () => {
    let finishCopy!: () => void;
    clipboard.mockImplementationOnce(() => new Promise<void>(resolve => { finishCopy = resolve; }));
    render(<NewsfeedShare post={post} />);
    await openShare();
    fireEvent.click(screen.getByRole("button", { name: "Copy caption" }));
    const trigger = screen.getByRole("button", { name: "Share", exact: true });
    fireEvent.click(trigger, { detail: 1 });
    fireEvent.click(trigger, { detail: 1 });
    await act(async () => finishCopy());
    expect(screen.queryByText("Caption copied.")).toBeNull();
  });

  it("uses existing post copy for captions and media without a rewrite request", async () => {
    render(<NewsfeedShare post={equityIssuance} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    await screen.findByAltText(`Portrait share preview: ${equityIssuance.title}`);
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(engine.buildShareCaption(equityIssuance));
    expect(engine.renderShareCard).toHaveBeenCalledWith(equityIssuance, undefined);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("mentions known banks by handle without guessing an unknown source handle", async () => {
    render(<NewsfeedShare post={{ ...post, title: "Goldman Sachs and J.P. Morgan", content: "Goldman sees supply grow. Synthetic Bank agrees.", source: { ...equityIssuance.source, publisher: "Synthetic Bank" } }} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    const caption = (screen.getByRole("textbox", { name: "Post caption" }) as HTMLTextAreaElement).value;
    expect(caption).toContain("@GoldmanSachs and @jpmorgan");
    expect(caption).toContain("@GoldmanSachs sees supply grow.");
    expect(caption).toContain("Synthetic Bank agrees.");
    expect(caption).not.toContain("@Synthetic");
    expect(caption).not.toContain("Source:");
  });

  it.each([
    { imageUrl: "/chart-1.png", provider: "Ramp", otherProvider: "Goldman Sachs" },
    { imageUrl: "/chart-2.png", provider: "Goldman Sachs", otherProvider: "Ramp" },
  ])("omits the $provider footer from copied and X captions while retaining media attribution", async ({ imageUrl, provider, otherProvider }) => {
    const attributed = {
      ...post,
      imageSources: { "/chart-1.png": "Ramp", "/chart-2.png": "Goldman Sachs" },
    };
    render(<NewsfeedShare post={attributed} imageUrl={imageUrl} />);
    await openShare();
    const caption = (screen.getByRole("textbox", { name: "Post caption" }) as HTMLTextAreaElement).value;
    expect(caption).toBe("Yen hedge demand\n\nHedge demand increased.");
    expect(caption).not.toContain("Source:");
    expect(caption).not.toContain(provider);
    expect(caption).not.toContain(otherProvider);
    fireEvent.click(screen.getByRole("button", { name: "Copy caption" }));
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith(caption));
    const compose = screen.getByRole("link", { name: "Compose on X" });
    expect(new URL(compose.getAttribute("href")!).searchParams.get("text")).toBe(caption);
    expect(engine.renderShareCard).toHaveBeenLastCalledWith(
      expect.objectContaining({ imageSources: attributed.imageSources }), imageUrl,
    );
  });

  it("keeps edited copy available when media fails and while retrying", async () => {
    engine.renderShareCard.mockRejectedValueOnce(new Error("Chart could not be loaded"))
      .mockImplementationOnce(() => new Promise(() => {}));
    render(<NewsfeedShare post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    expect((await screen.findByRole("alert")).textContent).toContain("Chart could not be loaded");
    const caption = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(caption.disabled).toBe(false);
    fireEvent.change(caption, { target: { value: "JPY hedge demand increased." } });
    const compose = screen.getByRole("link", { name: "Compose on X" });
    expect(new URL(compose.getAttribute("href")!).searchParams.get("text")).toBe(caption.value);
    fireEvent.click(screen.getByRole("button", { name: "Copy caption" }));
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith(caption.value));
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(engine.renderShareCard).toHaveBeenCalledTimes(2));
    expect(caption.value).toBe("JPY hedge demand increased.");
    expect(caption.disabled).toBe(false);
    expect(compose.getAttribute("aria-disabled")).not.toBe("true");
    expect((screen.getByRole("button", { name: "Copy caption" }) as HTMLButtonElement).disabled).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("prepares only on demand and uses the selected source chart", async () => {
    render(<NewsfeedShare post={post} imageUrl="/chart-2.png" />);
    expect(engine.renderShareCard).not.toHaveBeenCalled();
    await openShare();
    expect(engine.renderShareCard).toHaveBeenCalledWith(expect.objectContaining(post), "/chart-2.png");
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
    await waitFor(() => expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(false));
    expect((await screen.findByRole("alert")).textContent).toContain("Chart could not be loaded");
    expect(screen.getByRole("alert").closest("[data-toast-viewport]")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "My edited caption" } });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
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

  it("aborts recording at logical close and ignores its completion after a rapid reopen", async () => {
    let finishOld!: (blob: Blob) => void;
    let finishNew!: (blob: Blob) => void;
    engine.canvasToMp4.mockImplementationOnce(() => new Promise<Blob>(resolve => { finishOld = resolve; }))
      .mockImplementationOnce(() => new Promise<Blob>(resolve => { finishNew = resolve; }));
    render(<NewsfeedShare post={post} />);
    await openShare();
    fireEvent.click(screen.getByRole("button", { name: "Download Reels / TikTok video" }));
    const signal = engine.canvasToMp4.mock.calls[0][1] as AbortSignal;
    const trigger = screen.getByRole("button", { name: "Share", exact: true });
    fireEvent.click(trigger, { detail: 1 });
    expect(signal.aborted).toBe(true);
    expect(document.querySelector('[data-fade-state="exiting"][inert]')).not.toBeNull();
    fireEvent.click(trigger, { detail: 1 });
    await screen.findByAltText("Portrait share preview: Yen hedge demand");
    fireEvent.click(screen.getByRole("button", { name: "Download Reels / TikTok video" }));
    await act(async () => { finishOld(new Blob(["old"], { type: "video/mp4" })); });
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Exporting…" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { finishNew(new Blob(["new"], { type: "video/mp4" })); });
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce();
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
    expect(fetch).not.toHaveBeenCalled();
    expect(engine.renderShareCard).toHaveBeenLastCalledWith(expect.objectContaining(post), "/chart-2.png");
  });

  it("disables exports while the newly selected chart is rendering", async () => {
    const { rerender } = render(<NewsfeedShare post={post} imageUrl="/chart-1.png" />);
    await openShare();
    let resolveChart!: (canvas: HTMLCanvasElement) => void;
    const pendingChart = new Promise<HTMLCanvasElement>(resolve => { resolveChart = resolve; });
    engine.renderShareCard.mockReturnValueOnce(pendingChart);
    rerender(<NewsfeedShare post={post} imageUrl="/chart-2.png" />);
    await waitFor(() => expect(engine.renderShareCard).toHaveBeenLastCalledWith(expect.objectContaining(post), "/chart-2.png"));
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

describe("newsfeed share better-ui layout", () => {
  const css = readFileSync(join(__dirname, "..", "components", "NewsfeedShare.module.css"), "utf8");

  it("separates the share root with 14px margin to balance article border and footer", () => {
    expect(css).toMatch(/\.root\s*\{[^}]*margin-top:\s*14px/);
  });

  it("resets root margin-top on mobile shell where item flex gap provides spacing", () => {
    expect(css).toMatch(/:global\(body\[data-mobile="true"\]\)\s+\.root\s*\{[^}]*margin-top:\s*0/);
  });

  it("applies better-ui press scale and transition to interactive buttons", () => {
    expect(css).toMatch(/transition:\s*var\(--transition-press\)/);
    expect(css).toMatch(/transform:\s*scale\(var\(--press-scale\)\)/);
  });
});
