// Vision tagger — Anthropic Claude with image input. Used when a post has a
// local image; the chart often *is* the post and the text-only Cerebras tagger
// can't see it.
//
// Cost reference (claude-haiku-4-5): ~$0.003 / post, ~$4.40/mo at 50 posts/day.
// Cost reference (claude-sonnet-4-6): ~$0.009 / post, ~$13.90/mo at 50 posts/day.
//
// Network: undici defaults to IPv6, but api.anthropic.com's AAAA route is
// EHOSTUNREACH from residential IPv6 here (same as api.cerebras.ai). Force IPv4.

import fs from "node:fs";
import path from "node:path";
import { Agent, setGlobalDispatcher } from "undici";

import { __buildSystemPrompt as buildSystemPrompt, __normaliseTags as normaliseTags } from "./tagger.js";

let dispatcherConfigured = false;
function ensureIpv4Dispatcher() {
  if (dispatcherConfigured) return;
  setGlobalDispatcher(new Agent({ connect: { family: 4 } }));
  dispatcherConfigured = true;
}

const ENDPOINT = "https://api.anthropic.com/v1/messages";
const ANTHROPIC_VERSION = "2023-06-01";
const DEFAULT_MODEL = "claude-haiku-4-5";

const VISION_INSTRUCTION = [
  "An image is attached to this post (usually a chart). Read the image first:",
  "  - The instrument label, time window, chart type, and any annotated/highlighted candles or levels are PART of the post.",
  "  - When the chart highlights a specific candlestick pattern, indicator, or chart pattern, tag the SPECIFIC name (e.g. SHOOTING-STAR, HEAD-SHOULDERS, RSI).",
  "  - Numbers printed on axes or callouts are ground truth.",
  "Combine what you see in the image with the title and body, then pick the 3 best tags per the rules below.",
].join("\n");

function detectMediaType(filename) {
  const ext = path.extname(filename).toLowerCase();
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".gif") return "image/gif";
  if (ext === ".webp") return "image/webp";
  return null;
}

function resolveImageAbsolutePath(post, publicRoot) {
  if (!Array.isArray(post.images) || post.images.length === 0) return null;
  const rel = post.images[0];
  if (typeof rel !== "string" || rel.length === 0) return null;
  const trimmed = rel.replace(/^\//, "");
  return path.join(publicRoot, trimmed);
}

function buildUserPrompt(post) {
  const title = post.title || "(untitled)";
  const content = (post.content || "").slice(0, 1500);
  return `Title: ${title}\nBody: ${content}`;
}

function isRetryable(status) {
  return status === 429 || status >= 500;
}

async function callOnce({ model, systemPrompt, userPrompt, imageB64, mediaType, apiKey }) {
  ensureIpv4Dispatcher();
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": ANTHROPIC_VERSION,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model,
      max_tokens: 512,
      system: systemPrompt,
      messages: [
        {
          role: "user",
          content: [
            { type: "image", source: { type: "base64", media_type: mediaType, data: imageB64 } },
            { type: "text", text: `${VISION_INSTRUCTION}\n\n${userPrompt}\n\nReturn STRICT JSON: {"tags":["...","...","..."]}. No prose.` },
          ],
        },
      ],
    }),
  });

  if (isRetryable(res.status)) {
    const err = new Error(`vision-tagger ${model} ${res.status}`);
    err.retryable = true;
    throw err;
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`vision-tagger ${model} ${res.status} ${body.slice(0, 200)}`);
  }

  const json = await res.json();
  const text = json?.content?.find((c) => c.type === "text")?.text;
  if (typeof text !== "string") throw new Error("vision-tagger empty content");

  // Claude usually returns clean JSON; tolerate prose wrapping by extracting
  // the first {...} block.
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    const m = text.match(/\{[\s\S]*\}/);
    if (!m) throw new Error("vision-tagger non-JSON response");
    parsed = JSON.parse(m[0]);
  }
  if (!parsed || !Array.isArray(parsed.tags)) throw new Error("vision-tagger missing tags array");
  return parsed.tags;
}

export function createVisionTagger({
  apiKey = process.env.ANTHROPIC_API_KEY,
  model = DEFAULT_MODEL,
  publicRoot,
  getTaxonomySnapshot,
  readImage = (p) => fs.promises.readFile(p),
} = {}) {
  if (!apiKey) {
    throw new Error("createVisionTagger: ANTHROPIC_API_KEY is not set");
  }
  if (!publicRoot) {
    throw new Error("createVisionTagger: publicRoot is required");
  }
  if (typeof getTaxonomySnapshot !== "function") {
    throw new Error("createVisionTagger: getTaxonomySnapshot callback is required");
  }

  async function tagPost(post) {
    const imgPath = resolveImageAbsolutePath(post, publicRoot);
    if (!imgPath) return null;
    const mediaType = detectMediaType(imgPath);
    if (!mediaType) return null;

    let buf;
    try {
      buf = await readImage(imgPath);
    } catch (err) {
      console.warn(`[vision-tagger] missing image ${imgPath}: ${err.message}`);
      return null;
    }
    const imageB64 = Buffer.isBuffer(buf) ? buf.toString("base64") : Buffer.from(buf).toString("base64");

    const taxonomy = await getTaxonomySnapshot();
    const systemPrompt = buildSystemPrompt(taxonomy);
    const userPrompt = buildUserPrompt(post);

    try {
      const raw = await callOnce({ model, systemPrompt, userPrompt, imageB64, mediaType, apiKey });
      const tags = normaliseTags(raw).slice(0, 3);
      return tags.length === 3 ? tags : null;
    } catch (err) {
      console.warn(`[vision-tagger] ${model} error: ${err.message}`);
      return null;
    }
  }

  return { tagPost };
}

// Composes a vision-first tagger that falls back to a text tagger.
//   - If the post has a local image, try vision; on null, fall through.
//   - Otherwise (or on vision failure), use the text tagger.
// Returned object satisfies the same { tagPost } interface that hydrateTags expects.
export function createTaggerRouter({ visionTagger, textTagger }) {
  async function tagPost(post) {
    const hasImage = Array.isArray(post.images) && post.images.length > 0;
    if (hasImage && visionTagger) {
      const tags = await visionTagger.tagPost(post);
      if (tags) return tags;
    }
    if (textTagger) return textTagger.tagPost(post);
    return null;
  }
  return { tagPost };
}

// Test seam.
export const __resolveImageAbsolutePath = resolveImageAbsolutePath;
export const __detectMediaType = detectMediaType;
export const __buildUserPrompt = buildUserPrompt;
