// Open-vocabulary tagger for Market Ear posts.
// The model picks exactly 3 tags free-form; novel tags are auto-appended to
// data/tag_taxonomy.json by the caller (scraper or backfill). The existing
// taxonomy is shown to the model as context to encourage reuse.
//
// Provider: Cerebras (free tier, 30 rpm / 1M tok/day).
//   Primary  : gpt-oss-120b (reasoning model — needs ~800 tok budget for chain-of-thought)
//   Fallback : qwen-3-235b-a22b-instruct-2507
// Both verified on the active key via /v1/models.
//
// Network: undici defaults to IPv6, but api.cerebras.ai's AAAA route is
// EHOSTUNREACH from residential IPv6. Force IPv4 globally.

import { Agent, setGlobalDispatcher } from "undici";

let dispatcherConfigured = false;
function ensureIpv4Dispatcher() {
  if (dispatcherConfigured) return;
  setGlobalDispatcher(new Agent({ connect: { family: 4 } }));
  dispatcherConfigured = true;
}

const ENDPOINT = "https://api.cerebras.ai/v1/chat/completions";
const MODELS = ["gpt-oss-120b", "qwen-3-235b-a22b-instruct-2507"];

// All tags are uppercase. Multi-word concepts are kebab-cased then uppercased
// (e.g. "Put Call Ratio" → "PUT-CALL-RATIO"). Dedup is case-insensitive at the
// taxonomy layer, so the model returning "BTC" or "btc" or "Btc" all collapse
// to the single canonical "BTC".

function normaliseSingleTag(raw) {
  if (typeof raw !== "string") return null;
  let tag = raw.trim();
  if (tag.length === 0) return null;
  // Strip surrounding punctuation/quotes that LLMs sometimes emit.
  tag = tag.replace(/^[#"'`(\[]+|[\.,!?:;"'`)\]]+$/g, "");
  if (tag.length === 0) return null;

  tag = tag.toUpperCase();

  // Whitespace/underscore → hyphen (kebab-case for multi-word).
  tag = tag.replace(/[\s_]+/g, "-");
  // Drop characters outside [A-Z0-9-&]; collapse repeated hyphens; trim leading/trailing hyphens.
  tag = tag.replace(/[^A-Z0-9\-&]/g, "").replace(/-+/g, "-").replace(/^-|-$/g, "");

  return tag.length > 0 ? tag : null;
}

function normaliseTags(raw) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    const tag = normaliseSingleTag(item);
    if (!tag) continue;
    if (seen.has(tag)) continue;
    seen.add(tag);
    out.push(tag);
  }
  return out;
}

function buildSystemPrompt(taxonomy) {
  const existing = taxonomy.length > 0 ? taxonomy.join(", ") : "(none yet)";
  return [
    "You are a financial-news tagger for an institutional trading dashboard.",
    "",
    "Pick EXACTLY 3 tags that best capture the post's core themes.",
    "",
    "Priority order — apply each step and stop only when you have 3 tags:",
    "  1. INSTRUMENT or PRODUCT named in the post (puts, calls, options, BTC, oil, gold, futures, swaps, ETFs, bonds).",
    "  2. SECTOR or asset class focus (semis, energy, banks, credit, crypto, equities).",
    "  3. THEME or narrative (positioning, hedging, macro, Fed, inflation, earnings, geopolitics).",
    "",
    "Reuse an existing tag when one fits; coin a NEW tag only when nothing in the existing set captures the concept. Do not split a single concept across multiple near-synonyms.",
    "",
    "Naming rules — apply STRICTLY so tags merge cleanly across posts:",
    "  - ALL TAGS ARE UPPERCASE. No exceptions. Examples: BTC, OIL, VOL, PUTS, OPTIONS, POSITIONING, FED.",
    "  - Multi-word concepts use UPPERCASE kebab-case: PUT-CALL-RATIO, FUND-FLOWS, SINGLE-STOCK-VOL, DEALER-GAMMA, TAIL-HEDGE.",
    "  - Allowed characters: A-Z, 0-9, hyphen, ampersand. No spaces, no lowercase, no underscores.",
    "",
    "Disambiguation:",
    "  - VOL vs VIX: VIX only when the VIX index is explicitly named or charted; otherwise VOL.",
    "  - PUTS vs VOL: if the post is specifically about puts / put-call ratio / put protection, tag PUTS (not VOL).",
    "  - HEDGING is the action; PUTS/CALLS/OPTIONS are instruments — tag both when relevant.",
    "  - SKEW is options skew specifically.",
    "  - GAMMA is dealer-gamma / GEX.",
    "  - POSITIONING is who is long/short and how exposed.",
    "",
    "Output FORMAT: STRICT JSON. {\"tags\": [\"...\",\"...\",\"...\"]}. Exactly 3. No prose.",
    "",
    `Existing tags (reuse when possible): ${existing}`,
  ].join("\n");
}

function buildUserPrompt(post) {
  const title = post.title || "";
  const content = (post.content || "").slice(0, 1500);
  return `Title: ${title}\nBody: ${content}`;
}

function isRetryable(status) {
  return status === 429 || status >= 500;
}

async function callOnce(model, systemPrompt, userPrompt, apiKey) {
  ensureIpv4Dispatcher();
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userPrompt },
      ],
      response_format: { type: "json_object" },
      // gpt-oss-120b emits chain-of-thought into a separate `reasoning` field
      // before producing the final JSON in `content`. 800 leaves headroom; the
      // non-reasoning fallback ignores the extra budget.
      max_tokens: 800,
      temperature: 0.1,
    }),
  });

  if (isRetryable(res.status)) {
    const err = new Error(`tagger ${model} ${res.status}`);
    err.retryable = true;
    throw err;
  }
  if (!res.ok) {
    throw new Error(`tagger ${model} ${res.status}`);
  }

  const json = await res.json();
  const content = json?.choices?.[0]?.message?.content;
  if (typeof content !== "string") throw new Error("tagger empty content");

  let parsed;
  try {
    parsed = JSON.parse(content);
  } catch {
    throw new Error("tagger non-JSON response");
  }
  if (!parsed || !Array.isArray(parsed.tags)) throw new Error("tagger missing tags array");
  return parsed.tags;
}

export function createTagger({
  apiKey = process.env.CEREBRAS_API_KEY,
  getTaxonomySnapshot,
} = {}) {
  if (!apiKey) {
    throw new Error("createTagger: CEREBRAS_API_KEY is not set");
  }
  if (typeof getTaxonomySnapshot !== "function") {
    throw new Error("createTagger: getTaxonomySnapshot callback is required");
  }

  async function tagPost(post) {
    const taxonomy = await getTaxonomySnapshot();
    const systemPrompt = buildSystemPrompt(taxonomy);
    const userPrompt = buildUserPrompt(post);

    for (const model of MODELS) {
      try {
        const raw = await callOnce(model, systemPrompt, userPrompt, apiKey);
        const tags = normaliseTags(raw).slice(0, 3);
        if (tags.length === 3) return tags;
        // <3 valid tags after normalisation — try the next model.
      } catch (err) {
        if (!err.retryable) {
          console.warn(`[tagger] ${model} hard error: ${err.message}`);
        }
      }
    }
    return null;
  }

  return { tagPost };
}

// Test seam.
export const __normaliseTags = normaliseTags;
export const __normaliseSingleTag = normaliseSingleTag;

export async function hydrateTags(posts, tagger, { force = false, throttleMs = 0, onNewTags } = {}) {
  let updated = false;
  let first = true;
  for (const post of posts) {
    if (!force && Array.isArray(post.tags) && post.tags.length >= 3) continue;
    if (!first && throttleMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, throttleMs));
    }
    first = false;
    const tags = await tagger.tagPost(post);
    if (tags && tags.length >= 3) {
      post.tags = tags;
      updated = true;
      if (typeof onNewTags === "function") {
        await onNewTags(tags);
      }
    }
  }
  return updated;
}
