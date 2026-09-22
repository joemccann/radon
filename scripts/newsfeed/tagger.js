// Open-vocabulary tagger for Market Ear posts.
// The model picks exactly 3 tags free-form; novel tags are auto-appended to
// data/tag_taxonomy.json by the caller (scraper or backfill). The existing
// taxonomy is shown to the model as context to encourage reuse.
//
// Provider: shared model ladder (`scripts/clients/model_ladder.py`) via a
// thin Python CLI so Node cannot drift from Joe's order:
//   subscription: anthropic -> grok -> cursor -> codex -> gemini
//   nvidia (free)
//   cerebras (cheap paid, last)
// Soft-fails (returns null) when no keyed provider works.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const LADDER_CLI = fileURLToPath(new URL("../clients/model_ladder_cli.py", import.meta.url));
// Bound on ONE ladder walk; a timeout is a per-post tagging failure, never a
// cycle hang. R-466.
const DEFAULT_FETCH_TIMEOUT_MS = 30_000;

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
    "  1. TECHNICAL SIGNAL named explicitly — when the post calls out a candlestick pattern, indicator, chart pattern, or price-action concept, tag the SPECIFIC name. These are high-information signals; do not skip them.",
    "       Candlestick patterns: SHOOTING-STAR, HAMMER, INVERSE-HAMMER, HANGING-MAN, DOJI, ENGULFING, MORNING-STAR, EVENING-STAR, HARAMI, MARUBOZU, PIERCING-LINE, DARK-CLOUD-COVER, THREE-WHITE-SOLDIERS, THREE-BLACK-CROWS.",
    "       Chart patterns: HEAD-SHOULDERS, INVERSE-HEAD-SHOULDERS, DOUBLE-TOP, DOUBLE-BOTTOM, TRIPLE-TOP, TRIPLE-BOTTOM, TRIANGLE, ASCENDING-TRIANGLE, DESCENDING-TRIANGLE, FLAG, PENNANT, WEDGE, CUP-AND-HANDLE, BREAKOUT, BREAKDOWN, GAP, ISLAND-REVERSAL.",
    "       Indicators: RSI, MACD, MOVING-AVERAGE, GOLDEN-CROSS, DEATH-CROSS, BOLLINGER-BANDS, STOCHASTIC, ADX, ICHIMOKU, FIBONACCI, VWAP, OBV, ATR, PARABOLIC-SAR, KELTNER-CHANNEL.",
    "       Price-action: SUPPORT, RESISTANCE, TRENDLINE, OVERSOLD, OVERBOUGHT, DIVERGENCE, ELLIOTT-WAVE.",
    "       (Note: MOMENTUM, TREND, RANGE, PIVOT, MEAN-REVERSION are also legitimate tags — pick them when the post calls them out — but treat them as factor/macro concepts, NOT specifically TA. Tag MOMENTUM for momentum-factor / MoMo-basket posts; tag TREND for CTA/trend-following macro posts.)",
    "       Use the umbrella TECHNICAL-ANALYSIS only when the post discusses TA generically without naming a specific pattern/indicator.",
    "  2. INSTRUMENT or PRODUCT named in the post (puts, calls, options, BTC, oil, gold, futures, swaps, ETFs, bonds, SPX, SPY).",
    "  3. SECTOR or asset class focus (semis, energy, banks, credit, crypto, equities).",
    "  4. THEME or narrative (positioning, hedging, macro, Fed, inflation, earnings, geopolitics).",
    "",
    "Reuse an existing tag when one fits; coin a NEW tag only when nothing in the existing set captures the concept. Do not split a single concept across multiple near-synonyms.",
    "",
    "Naming rules — apply STRICTLY so tags merge cleanly across posts:",
    "  - ALL TAGS ARE UPPERCASE. No exceptions. Examples: BTC, OIL, VOL, PUTS, OPTIONS, POSITIONING, FED, RSI.",
    "  - Multi-word concepts use UPPERCASE kebab-case: PUT-CALL-RATIO, FUND-FLOWS, SINGLE-STOCK-VOL, DEALER-GAMMA, TAIL-HEDGE, SHOOTING-STAR, HEAD-SHOULDERS.",
    "  - Allowed characters: A-Z, 0-9, hyphen, ampersand. No spaces, no lowercase, no underscores.",
    "",
    "Disambiguation:",
    "  - VOL vs VIX: VIX only when the VIX index is explicitly named or charted; otherwise VOL.",
    "  - PUTS vs VOL: if the post is specifically about puts / put-call ratio / put protection, tag PUTS (not VOL).",
    "  - HEDGING is the action; PUTS/CALLS/OPTIONS are instruments — tag both when relevant.",
    "  - SKEW is options skew specifically.",
    "  - GAMMA is dealer-gamma / GEX.",
    "  - POSITIONING is who is long/short and how exposed.",
    "  - TECHNICAL SIGNALS: prefer the specific named pattern/indicator (SHOOTING-STAR, RSI, HEAD-SHOULDERS) over generic TECHNICAL-ANALYSIS. A post that names two TA concepts (e.g. shooting star AND inverse hammer) should tag both when slot count allows.",
    "  - CANDLESTICK is the umbrella; only use it when the post discusses candlestick analysis without naming a specific pattern.",
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

function resolvePythonBin() {
  const override = process.env.RADON_PYTHON_BIN;
  if (typeof override === "string" && override.trim()) return override.trim();
  return "python3.13";
}

export function completeViaLadder({
  system,
  instruction,
  timeoutMs = DEFAULT_FETCH_TIMEOUT_MS,
  spawnImpl = spawn,
  pythonBin = resolvePythonBin(),
  cliPath = LADDER_CLI,
} = {}) {
  return new Promise((resolve, reject) => {
    const child = spawnImpl(pythonBin, [cliPath], {
      stdio: ["pipe", "pipe", "pipe"],
      env: process.env,
    });
    let stdout = "";
    let settled = false;
    const finish = (err, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (err) reject(err);
      else resolve(value);
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(new Error("tagger timeout"));
    }, timeoutMs);

    child.stdout?.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr?.on("data", () => {});
    child.on("error", (err) => finish(err));
    child.on("close", () => {
      try {
        const parsed = JSON.parse(stdout);
        if (parsed && parsed.ok && parsed.data) finish(null, parsed.data);
        else finish(null, null);
      } catch {
        finish(null, null);
      }
    });

    child.stdin.write(JSON.stringify({
      system,
      instruction,
      accept: "tags",
      max_tokens: 800,
      timeout: Math.max(1, Math.ceil(timeoutMs / 1000)),
    }));
    child.stdin.end();
  });
}

export function createTagger({
  completeJson,
  getTaxonomySnapshot,
  timeoutMs = DEFAULT_FETCH_TIMEOUT_MS,
  spawnImpl,
  pythonBin,
} = {}) {
  if (typeof getTaxonomySnapshot !== "function") {
    throw new Error("createTagger: getTaxonomySnapshot callback is required");
  }
  const complete = typeof completeJson === "function"
    ? completeJson
    : (args) => completeViaLadder({
      ...args,
      timeoutMs: args.timeoutMs ?? timeoutMs,
      spawnImpl,
      pythonBin,
    });

  async function tagPost(post) {
    let timer;
    try {
      const taxonomy = await getTaxonomySnapshot();
      const parsed = await new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error("tagger timeout")), timeoutMs);
        Promise.resolve(complete({
          system: buildSystemPrompt(taxonomy),
          instruction: buildUserPrompt(post),
          timeoutMs,
        })).then(resolve, reject);
      });
      const tags = normaliseTags(parsed?.tags).slice(0, 3);
      if (tags.length === 3) return tags;
      return null;
    } catch (err) {
      console.warn(`[tagger] ladder failed: ${err.message}`);
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  return { tagPost };
}

// Test seam + shared with vision_tagger.js (uses identical prompt + normalisation).
export const __normaliseTags = normaliseTags;
export const __normaliseSingleTag = normaliseSingleTag;
export const __buildSystemPrompt = buildSystemPrompt;
export const DEFAULT_TAGGER_TIMEOUT_MS = DEFAULT_FETCH_TIMEOUT_MS;

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
