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
import { enrichWithParentTags } from "./tag_hierarchy.js";

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

  const parsed = parseTagsFromText(text);
  if (!parsed) throw new Error("vision-tagger non-JSON response");
  if (!Array.isArray(parsed.tags)) throw new Error("vision-tagger missing tags array");
  return parsed.tags;
}

// Walks the text and returns the first balanced {...} block. Tracks string
// literals so braces inside JSON strings don't confuse the depth counter.
// Used because Claude occasionally appends a second JSON object or trailing
// prose after the answer — a greedy /\{[\s\S]*\}/ would glue them together.
function extractFirstJsonObject(text) {
  let depth = 0;
  let start = -1;
  let inString = false;
  let escape = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escape) escape = false;
      else if (ch === "\\") escape = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{") {
      if (depth === 0) start = i;
      depth++;
    } else if (ch === "}") {
      depth--;
      if (depth === 0 && start !== -1) return text.slice(start, i + 1);
      if (depth < 0) return null;
    }
  }
  return null;
}

function parseTagsFromText(text) {
  // Fast path: response is already pure JSON.
  try {
    return JSON.parse(text);
  } catch {
    /* fall through to extraction */
  }
  // Extract balanced blocks one at a time and return the first one with a
  // tags array — handles "JSON + prose", "JSON + JSON", and code-fence wrapping.
  let cursor = 0;
  while (cursor < text.length) {
    const slice = text.slice(cursor);
    const block = extractFirstJsonObject(slice);
    if (!block) return null;
    try {
      const obj = JSON.parse(block);
      if (obj && Array.isArray(obj.tags)) return obj;
    } catch {
      /* try next block */
    }
    cursor += slice.indexOf(block) + block.length;
  }
  return null;
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

function unionTags(textTags, visionTags) {
  const seen = new Set();
  const merged = [];
  for (const t of [...(textTags || []), ...(visionTags || [])]) {
    if (typeof t !== "string" || t.length === 0) continue;
    if (seen.has(t)) continue;
    seen.add(t);
    merged.push(t);
  }
  return merged;
}

// Runs BOTH taggers per post (text + vision), in parallel, and stamps three
// fields on each post:
//   - tags_text   : exactly 3 from the Cerebras text tagger
//   - tags_vision : exactly 3 from the Anthropic vision tagger (only when a local image exists)
//   - tags        : union of the two, deduped, preserving order (text first)
//
// Cursor semantics:
//   - default mode: skip a post once both classifications are complete (or vision is N/A).
//   - force=true : always re-run both classifiers.
//
// Throttle is applied between posts (not within a post). Within-post text and
// vision calls run in parallel since they hit different providers.
export async function hydrateTagsDual(
  posts,
  { textTagger, visionTagger, force = false, throttleMs = 0, onNewTags } = {},
) {
  let updated = false;
  let firstApiCall = true;

  for (const post of posts) {
    const hasImage = Array.isArray(post.images) && post.images.length > 0;
    const textComplete = Array.isArray(post.tags_text) && post.tags_text.length === 3;
    const visionComplete =
      !hasImage || (Array.isArray(post.tags_vision) && post.tags_vision.length === 3);

    const needsText = !!textTagger && (force || !textComplete);
    const needsVision = hasImage && !!visionTagger && (force || !visionComplete);

    if (!needsText && !needsVision) continue;

    if (!firstApiCall && throttleMs > 0) {
      await new Promise((r) => setTimeout(r, throttleMs));
    }
    firstApiCall = false;

    const [textResult, visionResult] = await Promise.all([
      needsText ? textTagger.tagPost(post) : Promise.resolve(null),
      needsVision ? visionTagger.tagPost(post) : Promise.resolve(null),
    ]);

    let postUpdated = false;

    if (textResult && textResult.length === 3) {
      post.tags_text = textResult;
      postUpdated = true;
      if (typeof onNewTags === "function") await onNewTags(textResult);
    }
    if (visionResult && visionResult.length === 3) {
      post.tags_vision = visionResult;
      postUpdated = true;
      if (typeof onNewTags === "function") await onNewTags(visionResult);
    }

    if (postUpdated) {
      post.tags = enrichWithParentTags(unionTags(post.tags_text, post.tags_vision));
      updated = true;
    }
  }

  return updated;
}

// Test seam.
export const __resolveImageAbsolutePath = resolveImageAbsolutePath;
export const __detectMediaType = detectMediaType;
export const __buildUserPrompt = buildUserPrompt;
export const __extractFirstJsonObject = extractFirstJsonObject;
export const __parseTagsFromText = parseTagsFromText;
export const __unionTags = unionTags;
