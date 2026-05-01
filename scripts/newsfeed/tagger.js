// Cerebras-backed tagger for Market Ear posts.
// Primary: gpt-oss-120b (reasoning model — needs ~800 tok budget for chain-of-thought
// before final JSON). Fallback on 429/5xx/parse: qwen-3-235b-a22b-instruct-2507.
// Both are on the Cerebras free tier (verified via /v1/models on the active key).
//
// IPv4 pinning: themarketear.com's CDN AND api.cerebras.ai both advertise AAAA records
// that route as EHOSTUNREACH from this network. Node's fetch (undici) prefers IPv6;
// curl prefers IPv4. We bias undici toward IPv4 globally so that fetch() succeeds.

import { Agent, setGlobalDispatcher } from "undici";

let dispatcherConfigured = false;
function ensureIpv4Dispatcher() {
  if (dispatcherConfigured) return;
  setGlobalDispatcher(new Agent({ connect: { family: 4 } }));
  dispatcherConfigured = true;
}

const ENDPOINT = "https://api.cerebras.ai/v1/chat/completions";
const MODELS = ["gpt-oss-120b", "qwen-3-235b-a22b-instruct-2507"];

function buildPrompt(taxonomy, post) {
  const allowed = taxonomy.join(", ");
  const title = post.title || "";
  const content = (post.content || "").slice(0, 1200);
  return [
    "You classify financial-market news posts. Return strict JSON with a single key 'tags' whose value is an array of strings.",
    "Pick AT LEAST 3 tags from the allowed list below that best describe the post's themes. Prefer specificity (e.g. 'BTC' over just 'crypto' if both fit). Do NOT invent tags outside the allowed list.",
    "",
    `Allowed tags: ${allowed}`,
    "",
    `Title: ${title}`,
    `Body: ${content}`,
  ].join("\n");
}

function isRetryable(status) {
  return status === 429 || status >= 500;
}

async function callOnce(model, prompt, apiKey) {
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
        { role: "system", content: "Return only valid JSON: {\"tags\":[...]}." },
        { role: "user", content: prompt },
      ],
      response_format: { type: "json_object" },
      // gpt-oss-120b emits chain-of-thought into a separate `reasoning` field
      // before producing the final JSON in `content`. 800 leaves comfortable
      // headroom; non-reasoning fallbacks ignore the budget.
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
  return parsed.tags.map((t) => String(t).trim()).filter(Boolean);
}

function intersectWithTaxonomy(rawTags, taxonomySet) {
  const seen = new Set();
  const out = [];
  for (const tag of rawTags) {
    if (!taxonomySet.has(tag)) continue;
    if (seen.has(tag)) continue;
    seen.add(tag);
    out.push(tag);
  }
  return out;
}

export function createTagger({ taxonomy, apiKey = process.env.CEREBRAS_API_KEY } = {}) {
  if (!apiKey) {
    throw new Error("createTagger: CEREBRAS_API_KEY is not set");
  }
  if (!Array.isArray(taxonomy) || taxonomy.length === 0) {
    throw new Error("createTagger: taxonomy must be a non-empty array");
  }
  const taxonomySet = new Set(taxonomy);

  async function tagPost(post) {
    const prompt = buildPrompt(taxonomy, post);
    for (const model of MODELS) {
      try {
        const raw = await callOnce(model, prompt, apiKey);
        const filtered = intersectWithTaxonomy(raw, taxonomySet);
        if (filtered.length >= 3) return filtered;
        // Too few valid tags after intersection — try next model.
      } catch (err) {
        if (!err.retryable) {
          console.warn(`[tagger] ${model} hard error: ${err.message}`);
        }
        // Fall through to next model.
      }
    }
    return null;
  }

  return { tagPost };
}

export async function hydrateTags(posts, tagger, { force = false, throttleMs = 0 } = {}) {
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
    }
  }
  return updated;
}
