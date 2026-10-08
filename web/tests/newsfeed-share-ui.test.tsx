/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import NewsfeedShare, { resetNewsfeedShareVoiceCache } from "@/components/NewsfeedShare";
import { encodeVoiceEvent } from "@/lib/newsfeedVoiceProgress";

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
  resetNewsfeedShareVoiceCache();
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
  await waitFor(() => expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(false));
}

describe("news feed sharing", () => {

  it("shares the complete Morgan Stanley post before and after rewriting and cached reopening", async () => {
    const original = {
      ...post,
      id: "morgan-stanley-ai-cds",
      title: "Morgan Stanley: AI CDS basket trades ~45bp wider than CDX IG, and protection is still its preferred credit-derivatives expression",
      content: 'Morgan Stanley’s AI CDS basket now trades ~45bp wider than CDX IG.\n\nMS says buying CDS protection "remains our preferred way of playing the AI story in the credit derivatives market." The reason is not just a bearish default call. MS expects "increasing needs for non-economic hedging to manage counterparty exposure," even in a benign scenario where AI investments are profitable and continue.\n\nThat is an important distinction. The hedge bid can grow even if the capex cycle keeps working. More AI financing creates more counterparty exposure, and that creates more demand for protection.',
      source: { ...equityIssuance.source, publisher: "Tyler Durden", documentDate: "2026-10-07" },
    };
    let resolveRewrite!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(() => new Promise<Response>(resolve => { resolveRewrite = resolve; }));
    const { unmount } = render(<NewsfeedShare post={original} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    const expected = `${original.title}\n\n${original.content}`.replace(/Morgan Stanley|\bMS\b/g, "@morganstanley");
    const caption = screen.getByRole("textbox", { name: "Post caption" }) as HTMLTextAreaElement;
    expect(caption.value).toBe(expected);
    expect(expected.length).toBeGreaterThan(400);
    expect(new URL(screen.getByRole("link", { name: "Compose on X" }).getAttribute("href")!).searchParams.get("text")).toBe(expected);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await act(async () => { resolveRewrite({ ok: true, json: async () => ({ title: "Short AI credit hook", content: "Buy protection.", caption: "Short rewritten caption" }) } as Response); });
    await waitFor(() => expect(caption.disabled).toBe(false));
    expect(caption.value).toBe(expected);
    fireEvent.click(screen.getByRole("button", { name: "Copy caption" }));
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith(expected));
    unmount();
    render(<NewsfeedShare post={{ ...original }} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    expect((screen.getByRole("textbox", { name: "Post caption" }) as HTMLTextAreaElement).value).toBe(expected);
    expect(new URL(screen.getByRole("link", { name: "Compose on X" }).getAttribute("href")!).searchParams.get("text")).toBe(expected);
    expect(fetch).toHaveBeenCalledOnce();
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
    await waitFor(() => expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(false));
  });

  it("keeps the original caption while using the voice draft for image/video rendering", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ title: "Hedge demand is back.", content: "Positioning is neutral. Source: ZeroHedge" }) } as Response);
    render(<NewsfeedShare post={post} />);
    await openShare();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Yen hedge demand\n\nHedge demand increased.");
    expect(engine.renderShareCard).toHaveBeenCalledWith(expect.objectContaining({ title: "Hedge demand is back.", content: "Positioning is neutral." }), undefined);
    expect(fetch).toHaveBeenCalledWith("/api/newsfeed/share", expect.objectContaining({ method: "POST", cache: "no-store" }));
  });

  it.each([
    { imageUrl: "/chart-1.png", provider: "Ramp", otherProvider: "Goldman Sachs" },
    { imageUrl: "/chart-2.png", provider: "Goldman Sachs", otherProvider: "Ramp" },
  ])("omits the $provider footer from copied and X captions while retaining media attribution", async ({ imageUrl, provider, otherProvider }) => {
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
    // The preview renders from the original copy immediately; the rewrite never blocks it.
    await waitFor(() => expect(engine.renderShareCard).toHaveBeenCalledWith(post, undefined));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const signal = vi.mocked(fetch).mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    expect(signal?.aborted).toBe(true);
  });

  it("shows streamed rewrite progress with stage and elapsed time, then clears it", async () => {
    const encoder = new TextEncoder();
    let push!: (event: string, data: unknown) => void;
    let finish!: () => void;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (event, data) => controller.enqueue(encoder.encode(encodeVoiceEvent(event, data)));
        finish = () => controller.close();
      },
    });
    vi.mocked(fetch).mockResolvedValue(new Response(body, { headers: { "content-type": "text/event-stream; charset=utf-8" } }));
    render(<NewsfeedShare post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toMatchObject({ Accept: "text/event-stream" });
    const bar = screen.getByRole("progressbar", { name: "Voice rewrite progress" });
    expect(Number(bar.getAttribute("aria-valuenow"))).toBeGreaterThan(0);
    await act(async () => { push("stage", { stage: "queued" }); push("stage", { stage: "drafting" }); });
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Drafting in your voice…"));
    await act(async () => { push("stage", { stage: "hedging" }); });
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Still drafting. Trying a second model…"));
    expect(bar.getAttribute("aria-valuetext")).toMatch(/^Still drafting\. Trying a second model, \d+s elapsed$/);
    await act(async () => { push("stage", { stage: "checking" }); push("result", { title: "Hedge demand is back.", content: "Positioning remains neutral." }); finish(); });
    await waitFor(() => expect(screen.queryByRole("progressbar")).toBeNull());
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Yen hedge demand\n\nHedge demand increased.");
  });

  it("surfaces a streamed server error as the rewrite toast", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(encodeVoiceEvent("error", { error: "Rewrite timed out. Try again.", status: 504 }), { headers: { "content-type": "text/event-stream; charset=utf-8" } }));
    render(<NewsfeedShare post={post} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    await screen.findByText("Voice rewrite unavailable. Showing the original copy.");
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("preserves the original X intent after rewriting without waiting for the preview", async () => {
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
    await waitFor(() => expect(engine.renderShareCard).toHaveBeenLastCalledWith(expect.objectContaining({ title: "Hedge demand is back." }), undefined));
    expect(new URL(compose.getAttribute("href")!).searchParams.get("text")).toBe("Yen hedge demand\n\nHedge demand increased.");
    expect(compose.getAttribute("aria-disabled")).not.toBe("true");
    expect(screen.getByText("Preparing preview…")).not.toBeNull();
    expect((screen.getByRole("button", { name: "Download Story image" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("composes the equity-issuance fallback on X while rewrite is pending", async () => {
    vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
    render(<NewsfeedShare post={equityIssuance} />);
    fireEvent.click(screen.getByRole("button", { name: "Share", exact: true }));
    const compose = screen.getByRole("link", { name: "Compose on X" });
    const caption = (screen.getByRole("textbox") as HTMLTextAreaElement).value;
    const text = new URL(compose.getAttribute("href")!).searchParams.get("text")!;
    expect(text).toBe(caption);
    expect(caption.split("\n")[0]).toMatch(/\$252bn/i);
    expect(caption).toContain("$700bn");
    expect(caption).not.toMatch(/^[•●▪◦*-]\s/m);
    expect(caption.split("\n\n").length).toBeGreaterThan(1);
    expect(caption).not.toContain("Source:");
    expect(caption).toContain("@GoldmanSachs estimates");
    expect(caption).toContain("Supply overhang is a headwind, not a gale.");
    expect(caption.length).toBeGreaterThan(400);
    expect(compose.getAttribute("aria-disabled")).not.toBe("true");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(true);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const sent = JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body));
    expect(sent.title).toContain("$252bn");
    expect(sent.content).toContain("$700bn");
    expect(sent.content).toContain("Supply overhang is a headwind, not a gale.");
  });

  it("shows original copy on failure and retries voice generation", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
    render(<NewsfeedShare post={post} />);
    await openShare();
    expect((await screen.findByRole("alert")).textContent).toContain("Showing the original copy");
    expect(screen.getByRole("alert").closest("[data-toast-viewport]")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
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
    // Original-copy render and post-rewrite render both fail; the retry succeeds.
    engine.renderShareCard.mockRejectedValueOnce(new Error("Chart could not be loaded")).mockRejectedValueOnce(new Error("Chart could not be loaded"));
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
