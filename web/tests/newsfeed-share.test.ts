import { afterEach, describe, expect, it, vi } from "vitest";
import { assembleShareCaption, buildShareCaption, buildXShareUrl, canvasToMp4, canvasToPng, sanitizeShareText, SHARE_CAPTION_SOFT_CAP, supportsMp4Export, wrapShareText, type SharePost } from "../lib/newsfeedShare";

const post: SharePost = { id: "post-123", title: "Yen hedge demand jumps", content: "Positioning is near neutral.", timestamp: "2026-09-07", isoTimestamp: "2026-09-07T18:00:00Z", href: "https://themarketear.com/posts/post-123" };
const equityIssuance: SharePost = {
  id: "equity-issuance-252bn",
  title: "US corporates raised a record $252bn in 2Q; Goldman estimates ~$700bn total equity supply in 2026, significant portion AI-related",
  content: "US corporates raised a record $252bn in 2Q across IPOs, follow-ons, convertibles, and SPACs, per Goldman's Sarah Herring and Chris Hussey in their September 17, 2026 Midday Market Intelligence. The desk estimates total corporate equity supply will reach ~$700bn in 2026, a significant portion of which is AI-related. The driver here is straightforward: hyperscaler capex has eaten through free cash flow fast enough that companies are turning to public equity markets to fill the gap. This is not just a tech story. The surge in AI infrastructure investment is pulling capital broadly across the equity issuance complex. Goldman references Ben Snider's August 7 note (\"Equity issuance is a headwind but not a gale\") and Richard Ramsden's September 8 note (\"The next phase of capital markets growth and the AI infra impact\") for fuller context. The $700bn figure representing a meaningful supply overhang that competes with existing equity demand, even if the desk characterizes it as a headwind rather than a gale.",
  timestamp: "2026-09-20T20:06:00Z",
  isoTimestamp: "2026-09-20T20:06:00Z",
  href: "https://example.com/equity-issuance",
  source: {
    kind: "dropbox", publisher: "Goldman Midday Market Intelligence", documentDate: "2026-09-17",
    folderDate: "2026-09-20", pages: [1], figures: [], fileId: "id", revision: "r", contentHash: "h", url: "/private.pdf",
  },
};

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("social captions", () => {
  it("does not append image-provider attribution to X share captions", () => {
    const image = "https://media.radon.run/adoption.png";
    const attributed = { ...post, images: [image], imageSources: { [image]: "Ramp" } };
    expect(buildShareCaption(attributed)).toBe("Yen hedge demand jumps\n\nPositioning is near neutral.");
    expect(buildShareCaption(attributed)).not.toMatch(/market[\s-]*ear/i);
    const other = "https://media.radon.run/other.png";
    const multi = { ...attributed, images: [image, other], imageSources: { [image]: "Ramp", [other]: "Goldman Sachs" } };
    expect(buildShareCaption(multi, other)).toBe(buildShareCaption(attributed));
    expect(buildShareCaption(multi, other)).not.toContain("Ramp");
  });
  it("does not reuse an unrelated image provider or excluded publisher", () => {
    const image = "https://media.radon.run/adoption.png";
    const unrelated = { ...post, images: [image], imageSources: { "https://media.radon.run/other.png": "Ramp" } };
    expect(buildShareCaption(unrelated)).not.toContain("Source:");
    const excluded = { ...post, images: [image], imageSources: { [image]: "The Market Ear" } };
    expect(buildShareCaption(excluded)).not.toContain("Source:");
  });
  it.each(["—", "&mdash;", "&#8212;", "&#x2014;"])("sanitizes %s in source captions and manually edited X copy", dash => {
    const content = `Positioning ${dash} still neutral. Returns: -2.5%.`;
    expect(sanitizeShareText(content)).toBe("Positioning, still neutral. Returns: -2.5%.");
    const caption = buildShareCaption({ ...post, title: `Yen ${dash} the setup`, content });
    expect(caption).toBe("Yen, the setup\n\nPositioning, still neutral.\n\nReturns: -2.5%.");
    expect(new URL(buildXShareUrl(content)).searchParams.get("text")).toBe("Positioning, still neutral. Returns: -2.5%.");
  });
  it("removes publisher profile links and handles without orphan URLs", () => {
    const caption = buildShareCaption({ ...post, content: "Neutral positioning. https://x.com/zerohedge/status/123 https://twitter.com/themarketear @ZeroHedge @themarketear" });
    expect(caption).toBe("Yen hedge demand jumps\n\nNeutral positioning.");
  });

  it.each(["The Market Ear", "themarketear", "ZeroHedge", "ZERO HEDGE", "Zero-Hedge"])("excludes %s from embedded text and edited X captions", publisher => {
    const content = `Positioning is neutral. Source: ${publisher} https://www.zerohedge.com/markets/test https://themarketear.com/posts/test`;
    expect(buildShareCaption({ ...post, title: `Outlook via ${publisher}`, content })).not.toMatch(/market[\s-]*ear|zero[\s-]*hedge/i);
    expect(new URL(buildXShareUrl(content)).searchParams.get("text")).not.toMatch(/market[\s-]*ear|zero[\s-]*hedge/i);
  });
  it("omits feed attribution and preserves the full caption through the X intent", () => {
    const caption = buildShareCaption(post);
    expect(caption).toBe("Yen hedge demand jumps\n\nPositioning is near neutral.");
    const url = new URL(buildXShareUrl(caption));
    expect(url.origin + url.pathname).toBe("https://twitter.com/intent/tweet");
    expect(url.searchParams.get("text")).toBe(caption);
  });
  it("omits private research attribution, URLs and document IDs", () => {
    const privatePost: SharePost = { ...post, href: "/api/newsfeed/research/files/secret.pdf", source: {
      kind: "dropbox", publisher: "J.P. Morgan", documentDate: "2026-09-03", folderDate: "2026-09-07", pages: [2], figures: [],
      fileId: "file-private-id", revision: "private-revision", contentHash: "secret", url: "/api/newsfeed/research/files/secret.pdf",
    } };
    const caption = buildShareCaption(privatePost);
    expect(caption).toBe("Yen hedge demand jumps\n\nPositioning is near neutral.");
    expect(caption).not.toContain("Source:");
    expect(caption).not.toMatch(/secret|private|\/api\//);
  });
  it.each(["http://localhost:3000/newsfeed", "https://app.radon.run/api/newsfeed/research/files/secret.pdf", "https://10.0.0.1/private", "javascript:alert(1)", "https://themarketear.com@evil.test/posts/x", "/newsfeed"])("does not export unestablished public href %s", href => {
    expect(buildShareCaption({ ...post, href })).not.toContain(href);
  });
  it("removes authenticated research paths embedded in post text and strips permalink query secrets", () => {
    expect(buildShareCaption({ ...post, content: "Chart /api/newsfeed/research/files/secret.png", href: `${post.href}?token=secret` })).not.toContain("secret");
  });
  it("preserves the complete equity-issuance title, names, numbers and every sentence", () => {
    const caption = buildShareCaption(equityIssuance);
    expect(caption.split("\n\n")[0]).toBe(equityIssuance.title.replace("Goldman", "@GoldmanSachs"));
    expect(caption.length).toBeGreaterThan(SHARE_CAPTION_SOFT_CAP);
    expect(caption.replace(/\s+/g, " ")).toBe(
      `${equityIssuance.title} ${equityIssuance.content}`.replace(/\bGoldman\b/g, "@GoldmanSachs"),
    );
    expect(caption).toContain("$252bn");
    expect(caption).toContain("$700bn");
    expect(caption).toContain("Sarah Herring and Chris Hussey");
    expect(caption).toContain("Richard Ramsden");
    expect(caption).toContain("across IPOs, follow-ons, convertibles, and SPACs");
    expect(caption).toContain("The driver here is straightforward:");
    expect(caption).toContain("This is not just a tech story.");
    expect(caption).toContain("for fuller context.");
    expect(caption).toContain("even if the desk characterizes it as a headwind rather than a gale.");
    expect(caption).not.toContain("Source:");
    expect(new URL(buildXShareUrl(caption)).searchParams.get("text")).toBe(caption);
  });
  it("spaces all original bullet lines without adding a source footer", () => {
    const caption = buildShareCaption({
      ...equityIssuance,
      title: "US corps raised a record $252bn in 2Q equity supply",
      content: "• Goldman sees ~$700bn total equity supply in 2026\n• AI infra / hyperscaler capex is a big slice\n• Supply overhang = headwind, not a gale",
    });
    expect(caption).toBe(
      "US corps raised a record $252bn in 2Q equity supply\n\n"
      + "• @GoldmanSachs sees ~$700bn total equity supply in 2026\n\n"
      + "• AI infra / hyperscaler capex is a big slice\n\n"
      + "• Supply overhang = headwind, not a gale",
    );
  });
  it("shares the complete Morgan Stanley screenshot post with bank handles", () => {
    const title = "Morgan Stanley: AI CDS basket trades ~45bp wider than CDX IG, and protection is still its preferred credit-derivatives expression";
    const content = 'Morgan Stanley’s AI CDS basket now trades ~45bp wider than CDX IG.\n\nMS says buying CDS protection "remains our preferred way of playing the AI story in the credit derivatives market." The reason is not just a bearish default call. MS expects "increasing needs for non-economic hedging to manage counterparty exposure," even in a benign scenario where AI investments are profitable and continue.\n\nThat is an important distinction. The hedge bid can grow even if the capex cycle keeps working. More AI financing creates more counterparty exposure, and that creates more demand for protection.';
    const caption = buildShareCaption({ ...post, title, content, source: {
      kind: "dropbox", publisher: "Tyler Durden", documentDate: "2026-10-07", folderDate: "2026-10-07",
      pages: [13], figures: [], fileId: "private-id", revision: "r", contentHash: "h", url: "/private.pdf",
    } });
    expect(caption.startsWith(title.replace("Morgan Stanley", "@morganstanley") + "\n\n")).toBe(true);
    expect(caption).toContain("@morganstanley’s AI CDS basket now trades ~45bp wider than CDX IG.");
    expect(caption).toContain('@morganstanley says buying CDS protection "remains our preferred way of playing the AI story in the credit derivatives market."');
    expect(caption).toContain('The reason is not just a bearish default call.');
    expect(caption).toContain('@morganstanley expects "increasing needs for non-economic hedging to manage counterparty exposure," even in a benign scenario where AI investments are profitable and continue.');
    expect(caption.endsWith("More AI financing creates more counterparty exposure, and that creates more demand for protection.")).toBe(true);
    expect(caption.length).toBeGreaterThan(SHARE_CAPTION_SOFT_CAP);
    expect(caption).not.toMatch(/Source:|Tyler Durden|2026-10-07|\bMS\b|Morgan Stanley/);
    expect(new URL(buildXShareUrl(caption)).searchParams.get("text")).toBe(caption);
  });
  it("maps known names at word boundaries and leaves unknown names and existing handles intact", () => {
    const content = "MS, Morgan Stanley, Goldman Sachs and J.P. Morgan agree. @morganstanley @GoldmanSachs @jpmorgan agree too. MSFT, CMS and Acme Research disagree.";
    const caption = buildShareCaption({ ...post, title: "Bank outlook", content });
    expect(caption).toContain("@morganstanley, @morganstanley, @GoldmanSachs and @jpmorgan agree.");
    expect(caption).toContain("@morganstanley @GoldmanSachs @jpmorgan agree too.");
    expect(caption).toContain("MSFT, CMS and Acme Research disagree.");
    expect(caption).not.toContain("@@");
    expect(new URL(buildXShareUrl(caption)).searchParams.get("text")).toBe(caption);
  });
  it.each([
    ["Citigroup", "@Citi"], ["Citi", "@Citi"], ["Bank of America", "@BankofAmerica"],
    ["BofA", "@BankofAmerica"], ["Barclays", "@Barclays"], ["BNP Paribas", "@BNPParibas"],
    ["Bloomberg", "@business"], ["Reuters", "@Reuters"], ["Ramp", "@tryramp"],
  ])("uses the known source handle for %s", (name, handle) => {
    expect(buildShareCaption({ ...post, title: name, content: `${name} sees demand.` }))
      .toBe(`${handle}\n\n${handle} sees demand.`);
  });
  it("preserves URLs, email addresses, cashtags and lowercase ramp prose", () => {
    const content = "See https://example.com/Goldman and research@Goldman.com. $MS and MSFT rise as capex ramps up. The capex ramp continues.";
    expect(new URL(buildXShareUrl(content)).searchParams.get("text")).toBe(content);
  });
  it("removes any source line from manually edited X copy and preserves all other copy", () => {
    const caption = "MS sees hedging demand.\n\nSource: Tyler Durden · 2026-10-07\n\nGoldman Sachs sees issuance.\nSource: Acme Research\n\nJ.P. Morgan sees demand.";
    expect(new URL(buildXShareUrl(caption)).searchParams.get("text")).toBe("@morganstanley sees hedging demand.\n\n@GoldmanSachs sees issuance.\n\n@jpmorgan sees demand.");
  });
  it("assembles a voice caption as blank-line separated sentences", () => {
    const caption = assembleShareCaption("Seasonality", "• 104 to 130\n• Year 3, month +9.");
    expect(caption).toBe("Seasonality\n\n104 to 130.\n\nYear 3, month +9.");
  });
  it("drops a line that will not fit rather than cutting a sentence mid word", () => {
    const quoted = "The Energy Department said the SPR's minimum inventory is determined by \"cavern mechanics\", which the desk says translates to a conservative operational minimum of about 70 million barrels for the reserve overall.";
    const caption = assembleShareCaption("SPR is down to around 285 million barrels", `The oil buffer is getting thin. ${quoted}`);
    expect(caption).not.toContain("cavern");
    expect(caption).toBe("SPR is down to around 285 million barrels\n\nThe oil buffer is getting thin.");
  });
});

describe("card text wrapping", () => {
  const measure = (text: string) => text.length;
  it("wraps prose without losing words", () => expect(wrapShareText("yen hedge demand jumps", 12, measure)).toEqual(["yen hedge", "demand jumps"]));
  it("breaks oversized words and normalizes whitespace", () => {
    expect(wrapShareText("  abcdefghijklmnop\n xy  ", 5, measure)).toEqual(["abcde", "fghij", "klmno", "p xy"]);
  });
  it("handles empty content", () => expect(wrapShareText("", 50, measure)).toEqual([]));
});

function videoHarness(options: { unsupported?: boolean; constructorFailure?: boolean; empty?: boolean; wrongMime?: boolean; hang?: boolean; startFailure?: boolean } = {}) {
  const stopTrack = vi.fn();
  const draw = vi.fn();
  class Canvas {
    captureStream = vi.fn(() => ({ getTracks: () => [{ stop: stopTrack }] }));
    getContext = vi.fn(() => ({ drawImage: draw }));
  }
  Object.defineProperty(Canvas.prototype, "captureStream", { value: () => {} });
  let current: Recorder;
  class Recorder {
    static isTypeSupported = vi.fn((mime: string) => !options.unsupported && mime.startsWith("video/mp4"));
    state = "inactive";
    mimeType = options.wrongMime ? "video/webm" : "video/mp4";
    ondataavailable: ((event: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor() { if (options.constructorFailure) throw new Error("Encoder unavailable"); current = this; }
    start = vi.fn(() => { if (options.startFailure) throw new Error("Start failed"); this.state = "recording"; });
    stop = vi.fn(() => {
      this.state = "inactive";
      if (options.hang) return;
      if (!options.empty) this.ondataavailable?.({ data: new Blob(["mp4 payload"], { type: "video/mp4" }) });
      this.onstop?.();
    });
  }
  vi.stubGlobal("HTMLCanvasElement", Canvas);
  vi.stubGlobal("MediaRecorder", Recorder);
  return { canvas: new Canvas() as unknown as HTMLCanvasElement, stopTrack, draw, recorder: () => current! };
}

describe("MP4 export", () => {
  it("does not offer export when MP4 is unsupported", async () => {
    const { canvas } = videoHarness({ unsupported: true });
    expect(supportsMp4Export()).toBe(false);
    await expect(canvasToMp4(canvas)).rejects.toThrow("MP4 export is unavailable");
    expect(canvas.captureStream).not.toHaveBeenCalled();
  });
  it("records six seconds with fresh frames and releases capture tracks", async () => {
    vi.useFakeTimers();
    const harness = videoHarness();
    expect(supportsMp4Export()).toBe(true);
    const output = canvasToMp4(harness.canvas);
    vi.advanceTimersByTime(6000);
    const blob = await output;
    expect(blob.type).toBe("video/mp4");
    expect(blob.size).toBeGreaterThan(0);
    expect(harness.canvas.captureStream).toHaveBeenCalledWith(30);
    expect(harness.draw.mock.calls.length).toBeGreaterThanOrEqual(175);
    expect(harness.stopTrack).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each([{ constructorFailure: true }, { wrongMime: true }, { startFailure: true }])("cleans tracks when setup fails: %j", async options => {
    const harness = videoHarness(options);
    await expect(canvasToMp4(harness.canvas)).rejects.toThrow();
    expect(harness.stopTrack).toHaveBeenCalledOnce();
  });
  it("stops the recorder and tracks on cancellation", async () => {
    vi.useFakeTimers();
    const harness = videoHarness();
    const controller = new AbortController();
    const result = canvasToMp4(harness.canvas, controller.signal);
    const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await rejected;
    expect(harness.recorder().stop).toHaveBeenCalledOnce();
    expect(harness.stopTrack).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
  it("does not start capture after an existing cancellation", async () => {
    const harness = videoHarness();
    await expect(canvasToMp4(harness.canvas, AbortSignal.abort())).rejects.toMatchObject({ name: "AbortError" });
    expect(harness.canvas.captureStream).not.toHaveBeenCalled();
  });
  it.each([{ empty: true }, { hang: true }])("rejects failed encoder output and clears resources: %j", async options => {
    vi.useFakeTimers();
    const harness = videoHarness(options);
    const rejected = expect(canvasToMp4(harness.canvas)).rejects.toThrow();
    vi.advanceTimersByTime(12_000);
    await rejected;
    expect(harness.stopTrack).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
  it("handles asynchronous encoder errors", async () => {
    const harness = videoHarness();
    const rejected = expect(canvasToMp4(harness.canvas)).rejects.toThrow("Video encoding failed");
    harness.recorder().onerror?.();
    await rejected;
    expect(harness.stopTrack).toHaveBeenCalledOnce();
  });
});

describe("PNG export", () => {
  it("returns PNG bytes", async () => {
    const blob = new Blob(["png"], { type: "image/png" });
    const toBlob = vi.fn(callback => callback(blob));
    expect(await canvasToPng({ toBlob } as unknown as HTMLCanvasElement)).toBe(blob);
    expect(toBlob).toHaveBeenCalledWith(expect.any(Function), "image/png");
  });
  it.each([false, true])("rejects failed or tainted canvas exports (%s)", async throws => {
    const toBlob = (callback: (blob: Blob | null) => void) => { if (throws) throw new Error("tainted"); callback(null); };
    await expect(canvasToPng({ toBlob } as unknown as HTMLCanvasElement)).rejects.toThrow();
  });
});

// Rendering is tested independently of browser image codecs; E2E validates real pixels.
describe("caption sentence splitting", () => {
  it("does not split a line at a month abbreviation", async () => {
    const { assembleShareCaption } = await import("../lib/newsfeedShare");
    const caption = assembleShareCaption("Muse launch", "Meta launched Muse on Sept. 8 and it rattled the internet sector. EXPE fell 5.4%.");
    expect(caption).toContain("Meta launched Muse on Sept. 8 and it rattled the internet sector.");
  });
});

describe("share card rendering", () => {
  function renderHarness(imageFailure = false) {
    const fillText = vi.fn();
    const drawImage = vi.fn();
    const context = { fillText, drawImage, fillRect: vi.fn(), measureText: (text: string) => ({ width: text.length * 20 }) };
    const canvas = { width: 0, height: 0, getContext: () => context };
    vi.stubGlobal("document", { createElement: () => canvas, documentElement: {} });
    vi.stubGlobal("getComputedStyle", () => ({ getPropertyValue: () => "" }));
    vi.stubGlobal("window", { location: { origin: "https://app.radon.run" } });
    class ImageMock {
      naturalWidth = 1200;
      naturalHeight = 800;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(value: string) { if (value) queueMicrotask(() => imageFailure ? this.onerror?.() : this.onload?.()); }
    }
    vi.stubGlobal("Image", ImageMock);
    return { canvas, fillText, drawImage };
  }
  it("renders 1080 by 1920 text-only cards without requesting an image", async () => {
    const { renderShareCard } = await import("../lib/newsfeedShare");
    const harness = renderHarness();
    const canvas = await renderShareCard(post, undefined);
    expect(canvas).toMatchObject({ width: 1080, height: 1920 });
    expect(harness.drawImage).not.toHaveBeenCalled();
    expect(harness.fillText).toHaveBeenCalledWith("RADON", 72, 164);
    expect(harness.fillText.mock.calls.flat().join(" ")).not.toMatch(/market.?ear|zero.?hedge/i);
  });
  it("removes em dashes from every authored canvas surface", async () => {
    const { renderShareCard } = await import("../lib/newsfeedShare");
    const harness = renderHarness();
    await renderShareCard({ ...post, title: "Flows — still firm", content: "Demand &mdash; unchanged across desks. Range: 10—20%." }, undefined);
    const text = harness.fillText.mock.calls.map(call => call[0]).join(" ");
    expect(text).not.toMatch(/—|&(?:mdash|#8212|#x2014);/i);
    expect(text).toContain("Flows, still firm");
    expect(text).toContain("10 to 20%");
  });
  it.each(["The Market Ear", "ZeroHedge"])("excludes %s from every rendered text surface", async publisher => {
    const { renderShareCard } = await import("../lib/newsfeedShare");
    const harness = renderHarness();
    await renderShareCard({ ...post, title: `Outlook via ${publisher}`, content: `Neutral positioning across the desk. Source: ${publisher} https://zerohedge.com/test`, source: { kind: "dropbox", publisher, documentDate: "2026-09-03", folderDate: "2026-09-07", fileId: "id", revision: "r", contentHash: "h", url: "/private.pdf", pages: [2], figures: [{ url: "/chart.png", page: 2, caption: `Distribution via ${publisher}` }] } }, "/chart.png");
    const text = harness.fillText.mock.calls.map(call => call[0]).join(" ");
    expect(text).not.toMatch(/market[\s-]*ear|zero[\s-]*hedge|Source:/i);
    expect(text).toContain("Neutral positioning across the desk");
  });
  it("renders the X caption copy with today's date and no source, excerpt or figure footer", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-21T15:00:00Z"));
    try {
      const { renderShareCard, assembleShareCaption } = await import("../lib/newsfeedShare");
      const harness = renderHarness();
      const content = "Market positioning is near neutral across the desk. ".repeat(100);
      await renderShareCard({ ...post, content, source: { kind: "dropbox", publisher: "JPM", documentDate: "2026-09-03", folderDate: "2026-09-07", fileId: "private-id", revision: "r", contentHash: "h", url: "/private.pdf", pages: [2], figures: [{ url: "/chart.png", page: 2, caption: "USD/JPY put distribution" }] } }, "/chart.png");
      const text = harness.fillText.mock.calls.map(call => call[0]);
      expect(text).toContain("SEP 21, 2026");
      expect(text.join(" ")).not.toMatch(/SEP 3|EXCERPT|Source:|p\. 2|radon\.run|private-id/);
      const [hook, bullets] = assembleShareCaption(post.title, content).split("\n\n");
      expect(text).toContain(hook);
      for (const bullet of bullets.split("\n")) expect(text.join(" ")).toContain(bullet);
      const [, x, , width, height] = harness.drawImage.mock.calls[0];
      expect(width / height).toBeCloseTo(1.5);
      expect(x).toBeGreaterThanOrEqual(72);
      expect(height).toBeLessThanOrEqual(576);
    } finally { vi.useRealTimers(); }
  });
  it("fails visibly when the selected chart cannot load", async () => {
    const { renderShareCard } = await import("../lib/newsfeedShare");
    renderHarness(true);
    await expect(renderShareCard(post, "/missing.png")).rejects.toThrow("chart could not be loaded");
  });
});
