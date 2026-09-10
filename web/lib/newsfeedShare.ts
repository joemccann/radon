import type { MarketEarPost } from "./useNewsfeedPosts";
import { withoutEmDashes } from "./copyPunctuation";

export type SharePost = MarketEarPost & { href: string; isoTimestamp: string };

// Export assets retain Radon's publication identity, independently of app theme.
const INK = "#e2e8f0";
const MUTED = "#94a3b8";
const CANVAS = "#0a0f14";
const SIGNAL = "#05AD98";
const LINE = "#2e3947";

/** Shared outbound policy for captions, composer links and rendered card text. */
export function sanitizeShareText(text: string): string {
  return withoutEmDashes(text)
    .replace(/https?:\/\/(?:www\.)?(?:x|twitter)\.com\/(?:themarketear|zerohedge)(?:[/?#][^\s<>]*)?/gi, "")
    .replace(/(?:https?:\/\/|www\.)[^\s<>]*(?:themarketear|zerohedge)\.com[^\s<>]*/gi, "")
    .replace(/(?:\b(?:source|via|per|according to)\s*:?\s*)?@?\b(?:the[\s-]*)?(?:market[\s-]*ear|zero[\s-]*hedge)(?:\.com(?:\/[^\s]*)?)?\b/gi, "")
    .split("\n").map(line => line.replace(/[ \t]+/g, " ").trim().replace(/^[:;,·]\s*/, ""))
    .filter(line => !/^(?:source\s*:|[·,:;-])+$/i.test(line))
    .join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function shareSource(post: SharePost): string {
  const publisher = post.source?.publisher;
  if (!publisher || /(?:market[\s-]*ear|zero[\s-]*hedge)/i.test(publisher)) return "";
  return `Source: ${cleanText(publisher)}${post.source?.documentDate ? ` · ${post.source.documentDate}` : ""}`;
}

function cleanText(text: string): string {
  return sanitizeShareText(text).replace(/(?:https?:\/\/[^\s]*)?\/api\/newsfeed\/research\/[^\s)]+/gi, "")
    .replace(/\s+/g, " ").trim();
}

export function buildShareCaption(post: SharePost): string {
  return [cleanText(post.title), cleanText(post.content || ""), shareSource(post)]
    .filter(Boolean).join("\n\n");
}

export function buildXShareUrl(caption: string): string {
  return `https://twitter.com/intent/tweet?${new URLSearchParams({ text: sanitizeShareText(caption) })}`;
}

/** Break long words as well as prose, so provider text cannot escape the card. */
export function wrapShareText(text: string, maxWidth: number, measure: (text: string) => number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of cleanText(text).split(" ").filter(Boolean)) {
    if (line && measure(`${line} ${word}`) <= maxWidth) { line += ` ${word}`; continue; }
    if (line) { lines.push(line); line = ""; }
    for (const character of word) {
      if (line && measure(line + character) > maxWidth) { lines.push(line); line = ""; }
      line += character;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function drawText(ctx: CanvasRenderingContext2D, text: string, x: number, y: number,
  width: number, lineHeight: number, maxLines: number): { bottom: number; truncated: boolean } {
  const lines = wrapShareText(text, width, value => ctx.measureText(value).width);
  const shown = lines.slice(0, maxLines);
  const truncated = lines.length > maxLines;
  if (truncated && shown.length) {
    let last = shown[shown.length - 1];
    while (last && ctx.measureText(`${last}…`).width > width) last = last.slice(0, -1);
    shown[shown.length - 1] = `${last}…`;
  }
  shown.forEach((line, index) => ctx.fillText(line, x, y + index * lineHeight));
  return { bottom: y + shown.length * lineHeight, truncated };
}

function loadShareImage(source: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    const timer = setTimeout(() => finish(new Error("Chart loading timed out. Retry the export.")), 15_000);
    function finish(error?: Error) {
      clearTimeout(timer);
      image.onload = null;
      image.onerror = null;
      if (error) { image.src = ""; reject(error); } else resolve(image);
    }
    image.onload = () => image.naturalWidth > 0 && image.naturalHeight > 0
      ? finish() : finish(new Error("The chart could not be decoded."));
    image.onerror = () => finish(new Error("The chart could not be loaded. Retry the export."));
    try {
      const url = new URL(source, window.location.origin);
      if (!["http:", "https:", "blob:", "data:"].includes(url.protocol)) throw new Error("Unsupported chart URL.");
      image.src = url.origin === window.location.origin || ["blob:", "data:"].includes(url.protocol)
        ? url.href : `/_next/image?url=${encodeURIComponent(url.href)}&w=1920&q=75`;
    } catch (error) { finish(error instanceof Error ? error : new Error("Invalid chart URL.")); }
  });
}

export async function renderShareCard(post: SharePost, imageUrl: string | undefined): Promise<HTMLCanvasElement> {
  const image = imageUrl ? await loadShareImage(imageUrl) : undefined;
  // Existing application fonts are loaded before measurement, with a bounded fallback.
  if (document.fonts) await Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 1500))]);
  const styles = getComputedStyle(document.documentElement);
  const sans = styles.getPropertyValue("--font-sans").trim() || "Inter, sans-serif";
  const mono = styles.getPropertyValue("--font-mono").trim() || '"IBM Plex Mono", monospace';
  const canvas = document.createElement("canvas");
  canvas.width = 1080;
  canvas.height = 1920;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Image export is unavailable in this browser.");
  ctx.fillStyle = CANVAS;
  ctx.fillRect(0, 0, 1080, 1920);
  ctx.textBaseline = "top";
  ctx.fillStyle = SIGNAL;
  ctx.font = `600 32px ${sans}`;
  ctx.fillText("RADON", 72, 164);
  ctx.fillStyle = MUTED;
  ctx.font = `22px ${mono}`;
  ctx.fillText("MARKET ANALYSIS", 72, 220);
  const date = new Date(post.source?.documentDate ? `${post.source.documentDate}T12:00:00Z` : post.isoTimestamp);
  const dateLabel = Number.isFinite(date.getTime())
    ? date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).toUpperCase() : "";
  ctx.fillText(dateLabel, 1080 - 72 - ctx.measureText(dateLabel).width, 220);
  ctx.fillStyle = LINE;
  ctx.fillRect(72, 269, 936, 2);
  ctx.fillStyle = INK;
  ctx.font = `600 54px ${sans}`;
  const title = drawText(ctx, post.title, 72, 312, 900, 65, 4);
  let bodyY = title.bottom + 30;
  if (image) {
    // A white chart field preserves the original chart, labels, axes and source.
    const top = bodyY;
    const height = 600;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(72, top, 936, height);
    const scale = Math.min(912 / image.naturalWidth, (height - 24) / image.naturalHeight);
    const width = image.naturalWidth * scale;
    const scaledHeight = image.naturalHeight * scale;
    ctx.drawImage(image, 72 + (936 - width) / 2, top + (height - scaledHeight) / 2, width, scaledHeight);
    bodyY += height + 32;
  }
  ctx.fillStyle = INK;
  ctx.font = `32px ${sans}`;
  const body = drawText(ctx, post.content || "", 72, bodyY, 900, 45, Math.max(1, Math.floor((1480 - bodyY) / 45)));
  if (title.truncated || body.truncated) {
    ctx.fillStyle = MUTED;
    ctx.font = `22px ${mono}`;
    ctx.fillText("EXCERPT", 72, 1490);
  }
  ctx.fillStyle = LINE;
  ctx.fillRect(72, 1535, 936, 2);
  ctx.fillStyle = MUTED;
  ctx.font = `24px ${sans}`;
  drawText(ctx, shareSource(post), 72, 1554, 900, 30, 2);
  const figure = post.source?.figures.find(item => item.url === imageUrl);
  if (figure) {
    ctx.font = `22px ${sans}`;
    drawText(ctx, `p. ${figure.page} · ${figure.caption}`, 72, 1622, 900, 28, 2);
  }
  ctx.font = `22px ${mono}`;
  ctx.fillText("radon.run", 72, 1720);
  return canvas;
}

export function canvasToPng(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    try {
      canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("Image export failed. Retry the export.")), "image/png");
    } catch { reject(new Error("The chart could not be exported. Reload it and retry.")); }
  });
}

const MP4_TYPES = ['video/mp4;codecs=avc1.42E01E', 'video/mp4;codecs=avc1', 'video/mp4'];
function mp4Type(): string | undefined {
  if (typeof MediaRecorder === "undefined" || typeof HTMLCanvasElement === "undefined"
    || typeof HTMLCanvasElement.prototype.captureStream !== "function") return undefined;
  return MP4_TYPES.find(type => MediaRecorder.isTypeSupported(type));
}
export function supportsMp4Export(): boolean { return Boolean(mp4Type()); }

/** Six seconds of genuine MP4; never relabel a WebM recording as an MP4. */
export async function canvasToMp4(canvas: HTMLCanvasElement, signal?: AbortSignal): Promise<Blob> {
  if (signal?.aborted) throw new DOMException("Export cancelled", "AbortError");
  const mimeType = mp4Type();
  if (!mimeType) throw new Error("MP4 export is unavailable in this browser. Download the PNG or try Chrome or Safari.");
  const stream = canvas.captureStream(30);
  return new Promise((resolve, reject) => {
    let recorder: MediaRecorder | undefined;
    let settled = false;
    let stopTimer: ReturnType<typeof setTimeout> | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    let frames: ReturnType<typeof setInterval> | undefined;
    const chunks: Blob[] = [];
    const finish = (error?: Error, blob?: Blob) => {
      if (settled) return;
      settled = true;
      clearTimeout(stopTimer);
      clearTimeout(watchdog);
      clearInterval(frames);
      signal?.removeEventListener("abort", abort);
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        recorder.onerror = null;
        if (recorder.state !== "inactive") { try { recorder.stop(); } catch { /* Tracks still stop below. */ } }
      }
      stream.getTracks().forEach(track => track.stop());
      if (error) reject(error); else resolve(blob!);
    };
    const abort = () => finish(new DOMException("Export cancelled", "AbortError"));
    signal?.addEventListener("abort", abort, { once: true });
    try {
      if (signal?.aborted) { abort(); return; }
      recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 6_000_000 });
      if (!/^video\/mp4(?:;|$)/i.test(recorder.mimeType)) throw new Error("This browser did not select an MP4 encoder.");
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => finish(new Error("Video encoding failed. Download the PNG or retry."));
      recorder.onstop = () => chunks.length
        ? finish(undefined, new Blob(chunks, { type: "video/mp4" }))
        : finish(new Error("Video encoding produced an empty file. Retry the export."));
      recorder.start(250);
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("Video capture is unavailable.");
      // captureStream only emits frames when the canvas is painted.
      frames = setInterval(() => ctx.drawImage(canvas, 0, 0), 1000 / 30);
      stopTimer = setTimeout(() => { try { recorder?.stop(); } catch { finish(new Error("Video export could not finish.")); } }, 6000);
      watchdog = setTimeout(() => finish(new Error("Video export timed out. Retry the export.")), 12_000);
    } catch (error) { finish(error instanceof Error ? error : new Error("Video export failed.")); }
  });
}
