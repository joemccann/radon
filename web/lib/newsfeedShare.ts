import type { MarketEarPost } from "./useNewsfeedPosts";
import { getImageSource } from "./newsfeedSource";
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

function shareSource(post: SharePost, imageUrl = post.images?.[0]): string {
  const publisher = post.source?.publisher ?? getImageSource(post, imageUrl);
  if (!publisher || /(?:market[\s-]*ear|zero[\s-]*hedge)/i.test(publisher)) return "";
  return `Source: ${cleanText(publisher)}${post.source?.documentDate ? ` · ${post.source.documentDate}` : ""}`;
}

function cleanText(text: string): string {
  return sanitizeShareText(text).replace(/(?:https?:\/\/[^\s]*)?\/api\/newsfeed\/research\/[^\s)]+/gi, "")
    .replace(/\s+/g, " ").trim();
}

export const SHARE_CAPTION_SOFT_CAP = 400;
const HOOK_MAX = 110;
const BULLET_MAX = 88;
const BULLET_PREFIX = /^(?:[•●▪◦]|[-*])\s+/;
const IMPLICATION = /\b(?:headwind|overhang|not a gale|rather than|this (?:is|means|leaves)|implies?)\b/i;
const FILLER = /^(?:this is not just a tech story|for fuller context|the driver here is straightforward)[.!]?$/i;
const THROAT_CLEAR = /^(?:the driver here is straightforward:\s*|the (?:desk|report|note|authors?) (?:estimates?|sees|says|notes|finds|concludes|suggests)(?: that)?\s+)/i;
const NOTE_REF = /\breferences?\b.+\bnote\b/i;

function stripResearchPaths(text: string): string {
  return text.replace(/(?:https?:\/\/[^\s]*)?\/api\/newsfeed\/research\/[^\s)]+/gi, "");
}

function captionLines(text: string): string[] {
  return sanitizeShareText(stripResearchPaths(text))
    .split("\n")
    .map(line => line.replace(/[ \t]+/g, " ").trim())
    .filter(line => line && !/^source\s*:/i.test(line));
}

function captionProse(text: string): string {
  return captionLines(text).join(" ");
}

function compactPhrase(text: string, max: number): string {
  const cleaned = text.replace(/\s+/g, " ").trim().replace(/[.;,]+$/, "");
  const cut = cleaned.length <= max ? cleaned : (() => {
    const slice = cleaned.slice(0, max);
    const punct = Math.max(slice.lastIndexOf(";"), slice.lastIndexOf(","));
    const clause = slice.search(/\s+(?:that|fast enough|even if)\b/);
    const at = punct >= 32 ? punct : clause >= 32 ? clause : slice.lastIndexOf(" ");
    return (at > Math.min(32, max >> 1) ? slice.slice(0, at) : slice).trim().replace(/[.;,]+$/, "");
  })();
  return cut.replace(/\s+(?:of|are|that|the|a|an|and|to|for|with|from|in)$/i, "").trim();
}

function leadCap(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function splitSentences(text: string): string[] {
  return text.replace(/\n+/g, " ").split(/(?<!\b(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec|No|vs|Mr|Ms|Dr|St)\.)(?<=[.!?])\s+(?=[A-Z("'“]|\d|~|\$)/).map(part => part.trim()).filter(Boolean);
}

function numbersIn(text: string): string[] {
  return text.match(/[$€£~]?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|bps|bn|tn|mn|[kmbt]\b))?/gi)
    ?.map(value => value.toLowerCase().replace(/\s/g, "").replace(/^~/, "")) ?? [];
}

function overlapRatio(left: string, right: string): number {
  const words = (text: string) => new Set((text.toLowerCase().match(/[a-z0-9$~]+/g) ?? []).filter(word => word.length > 2));
  const a = words(left);
  if (!a.size) return 0;
  const b = words(right);
  let shared = 0;
  for (const word of a) if (b.has(word)) shared += 1;
  return shared / a.size;
}

function restatesHook(sentence: string, hook: string): boolean {
  const left = sentence.toLowerCase().replace(/[.;,]+$/, "");
  const right = hook.toLowerCase().replace(/[.;,]+$/, "");
  return !right || left.startsWith(right) || right.startsWith(left) || overlapRatio(hook, sentence) >= 0.8;
}

function hookHeadline(title: string, content: string): string {
  const titleText = captionProse(title);
  const raw = titleText || splitSentences(captionProse(content))[0] || "";
  if (!raw) return "";
  const clauses = raw.split(/\s*;\s*/).map(part => part.trim()).filter(Boolean);
  const numbered = clauses.find(clause => /[$€£~]?\d/.test(clause) && clause.length <= HOOK_MAX);
  return compactPhrase(numbered || clauses[0] || raw, HOOK_MAX);
}

function asBullet(sentence: string): string {
  return `• ${leadCap(compactPhrase(sentence.replace(THROAT_CLEAR, "").replace(BULLET_PREFIX, ""), BULLET_MAX))}`;
}

function extractSharePoints(content: string, hook: string): { bullets: string[]; implication: string } {
  const lines = captionLines(content);
  const structured = lines.filter(line => BULLET_PREFIX.test(line)).map(asBullet);
  if (structured.length) {
    const rest = lines.filter(line => !BULLET_PREFIX.test(line) && !FILLER.test(line)).join(" ");
    return { bullets: structured.slice(0, 4), implication: rest && IMPLICATION.test(rest) ? compactPhrase(rest, 72) : "" };
  }
  const used = new Set(numbersIn(hook));
  const sentences = splitSentences(captionProse(content))
    .map(sentence => sentence.replace(THROAT_CLEAR, "").trim())
    .filter(sentence => (sentence.length >= 20 || numbersIn(sentence).length > 0) && !FILLER.test(sentence) && !NOTE_REF.test(sentence) && !restatesHook(sentence, hook));
  const facts: string[] = [];
  let implication = "";
  for (const sentence of sentences) {
    const unused = numbersIn(sentence).some(value => !used.has(value));
    if (IMPLICATION.test(sentence) && !unused) { if (!implication) implication = compactPhrase(sentence, 72); continue; }
    if (unused || facts.length < 2) {
      facts.push(sentence);
      for (const value of numbersIn(sentence)) used.add(value);
    }
    if (facts.length >= 3) break;
  }
  if (facts.length < 2) {
    for (const sentence of sentences) {
      if (facts.includes(sentence) || compactPhrase(sentence, BULLET_MAX) === implication) continue;
      facts.push(sentence);
      if (facts.length >= 2) break;
    }
  }
  return { bullets: facts.slice(0, 4).map(asBullet), implication };
}

export function assembleShareCaption(title: string, content: string, source = ""): string {
  const hook = hookHeadline(title, content);
  const { bullets, implication } = extractSharePoints(content, hook);
  const sourceLine = sanitizeShareText(source).split("\n").map(line => line.replace(/[ \t]+/g, " ").trim()).filter(Boolean).join(" ");
  const join = (items: string[], extra = implication) => [hook, items.join("\n"), extra, sourceLine].filter(Boolean).join("\n\n");
  const items = [...bullets];
  let caption = join(items);
  while (caption.length > SHARE_CAPTION_SOFT_CAP && items.length > 2) { items.pop(); caption = join(items); }
  if (caption.length > SHARE_CAPTION_SOFT_CAP && implication) caption = join(items, "");
  if (caption.length > SHARE_CAPTION_SOFT_CAP && items.length > 1) { items.pop(); caption = join(items, ""); }
  return sanitizeShareText(caption);
}

export function buildShareCaption(post: SharePost, imageUrl = post.images?.[0]): string {
  return assembleShareCaption(post.title, post.content || "", shareSource(post, imageUrl));
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
  // Story cards carry the export date (ET), not the source document date.
  const dateLabel = new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/New_York" }).toUpperCase();
  ctx.fillText(dateLabel, 1080 - 72 - ctx.measureText(dateLabel).width, 220);
  ctx.fillStyle = LINE;
  ctx.fillRect(72, 269, 936, 2);
  // Same copy and layout as the X caption: hook, then bullets and implication.
  const [hook = post.title, ...paragraphs] = assembleShareCaption(post.title, post.content || "").split("\n\n");
  ctx.fillStyle = INK;
  ctx.font = `600 54px ${sans}`;
  const title = drawText(ctx, hook, 72, 312, 900, 65, 4);
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
  const limit = 1840;
  for (const [index, paragraph] of paragraphs.entries()) {
    if (index) bodyY += 22;
    for (const line of paragraph.split("\n")) {
      const room = Math.floor((limit - bodyY) / 45);
      if (room < 1) break;
      bodyY = drawText(ctx, line, 72, bodyY, 900, 45, room).bottom + 8;
    }
  }
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
