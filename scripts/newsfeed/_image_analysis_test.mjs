// One-shot evaluation: Claude vision on the latest N newsfeed images.
// Reports per-call analysis + token usage + USD cost.
// Run: node scripts/newsfeed/_image_analysis_test.mjs [N=3] [model=claude-sonnet-4-6]

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Agent, setGlobalDispatcher } from "undici";

// api.anthropic.com's AAAA route is EHOSTUNREACH from residential IPv6 here
// (same pattern as api.cerebras.ai). Force IPv4.
setGlobalDispatcher(new Agent({ connect: { family: 4 } }));

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), "../..");
const POSTS_PATH = path.join(REPO_ROOT, "web/public/data/posts.json");
const MEDIA_ROOT = path.join(REPO_ROOT, "web/public");
const ENV_PATH = path.join(REPO_ROOT, "web/.env");

function loadEnv(p) {
  const raw = fs.readFileSync(p, "utf8");
  for (const line of raw.split("\n")) {
    const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (!m) continue;
    if (process.env[m[1]]) continue;
    let v = m[2];
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    process.env[m[1]] = v;
  }
}

loadEnv(ENV_PATH);

const N = parseInt(process.argv[2] || "3", 10);
const MODEL = process.argv[3] || "claude-sonnet-4-6";

// Public list pricing per million tokens (USD).
const PRICING = {
  "claude-sonnet-4-6": { input: 3.0, output: 15.0 },
  "claude-haiku-4-5": { input: 1.0, output: 5.0 },
  "claude-opus-4-7": { input: 15.0, output: 75.0 },
};

const ANALYSIS_PROMPT = [
  "You are a market-structure analyst evaluating a chart attached to a financial news post.",
  "",
  "Identify, in order, what is actually visible in the image:",
  "  1. INSTRUMENT: the ticker / index / asset depicted (read the chart label).",
  "  2. CHART TYPE: candlestick, line, bar, area, heatmap, table, etc.",
  "  3. TIME WINDOW: visible date range (e.g. Sep 2025 – Jul 2026).",
  "  4. TECHNICAL SIGNALS: candlestick patterns (shooting star, hammer, doji, engulfing…), chart patterns (head-shoulders, double-top, breakout…), indicators (RSI, MACD, MAs…), price-action concepts (support, resistance, divergence). Name the SPECIFIC pattern when one is highlighted.",
  "  5. ANNOTATIONS: any boxes, arrows, callouts, highlighted candles, drawn levels.",
  "  6. KEY LEVELS / NUMBERS: explicit values printed on axes or callouts.",
  "  7. THREE-TAG SUGGESTION: the 3 best UPPERCASE-KEBAB tags for this post given chart + caption.",
  "",
  "Be terse. Pure observation, no speculation. Return STRICT JSON with keys: instrument, chart_type, time_window, technical_signals (array), annotations (array), key_levels (array), suggested_tags (array of exactly 3).",
].join("\n");

function buildUserPrompt(post) {
  return [
    `Post title: ${post.title || "(untitled)"}`,
    `Post body: ${(post.content || "").slice(0, 1500)}`,
    `Existing tags: ${(post.tags || []).join(", ") || "(none)"}`,
    "",
    "Analyse the attached image.",
  ].join("\n");
}

async function callAnthropic({ apiKey, model, userPrompt, imageBase64, mediaType }) {
  const body = {
    model,
    max_tokens: 1024,
    messages: [
      {
        role: "user",
        content: [
          { type: "image", source: { type: "base64", media_type: mediaType, data: imageBase64 } },
          { type: "text", text: userPrompt },
        ],
      },
    ],
    system: ANALYSIS_PROMPT,
  };

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const json = await res.json();
  if (!res.ok) {
    throw new Error(`anthropic ${res.status}: ${JSON.stringify(json).slice(0, 400)}`);
  }
  return json;
}

function costUSD(model, usage) {
  const p = PRICING[model];
  if (!p) return null;
  const inTokens = (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0) + (usage.cache_creation_input_tokens || 0);
  const outTokens = usage.output_tokens || 0;
  return {
    input_tokens: inTokens,
    output_tokens: outTokens,
    input_usd: (inTokens / 1_000_000) * p.input,
    output_usd: (outTokens / 1_000_000) * p.output,
    total_usd: (inTokens / 1_000_000) * p.input + (outTokens / 1_000_000) * p.output,
  };
}

function detectMediaType(filename) {
  const ext = path.extname(filename).toLowerCase();
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".gif") return "image/gif";
  if (ext === ".webp") return "image/webp";
  return "image/png";
}

async function main() {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) throw new Error("ANTHROPIC_API_KEY not set in web/.env");

  const posts = JSON.parse(fs.readFileSync(POSTS_PATH, "utf8"));
  const candidates = posts.filter((p) => Array.isArray(p.images) && p.images.length > 0).slice(0, N);
  if (candidates.length === 0) throw new Error("no posts with images");

  console.log(`model: ${MODEL}`);
  console.log(`posts:  ${candidates.length}`);
  console.log("");

  const results = [];

  for (const [i, post] of candidates.entries()) {
    const imgRel = post.images[0].replace(/^\//, "");
    const imgPath = path.join(MEDIA_ROOT, imgRel);
    const imgBuf = fs.readFileSync(imgPath);
    const imgB64 = imgBuf.toString("base64");
    const mediaType = detectMediaType(imgPath);

    const t0 = Date.now();
    const resp = await callAnthropic({
      apiKey,
      model: MODEL,
      userPrompt: buildUserPrompt(post),
      imageBase64: imgB64,
      mediaType,
    });
    const elapsedMs = Date.now() - t0;

    const text = resp.content?.find((c) => c.type === "text")?.text || "";
    let parsed = null;
    try {
      const m = text.match(/\{[\s\S]*\}/);
      if (m) parsed = JSON.parse(m[0]);
    } catch {}

    const cost = costUSD(MODEL, resp.usage || {});

    results.push({ post, parsed, raw: text, cost, elapsedMs, imageBytes: imgBuf.length });

    console.log(`────────── [${i + 1}/${candidates.length}] ${post.id} • ${post.title} ──────────`);
    console.log(`image       : ${imgRel} (${(imgBuf.length / 1024).toFixed(1)} KB)`);
    console.log(`elapsed     : ${elapsedMs} ms`);
    console.log(`tokens      : in=${cost?.input_tokens}  out=${cost?.output_tokens}`);
    console.log(`cost        : input=$${cost?.input_usd.toFixed(6)}  output=$${cost?.output_usd.toFixed(6)}  total=$${cost?.total_usd.toFixed(6)}`);
    console.log(`existing    : ${(post.tags || []).join(", ")}`);
    if (parsed) {
      console.log(`instrument  : ${parsed.instrument}`);
      console.log(`chart_type  : ${parsed.chart_type}`);
      console.log(`time_window : ${parsed.time_window}`);
      console.log(`signals     : ${(parsed.technical_signals || []).join(" | ")}`);
      console.log(`annotations : ${(parsed.annotations || []).join(" | ")}`);
      console.log(`key_levels  : ${(parsed.key_levels || []).join(" | ")}`);
      console.log(`suggested   : ${(parsed.suggested_tags || []).join(", ")}`);
    } else {
      console.log("⚠ could not parse JSON; raw text:");
      console.log(text.slice(0, 800));
    }
    console.log("");
  }

  const totals = results.reduce(
    (acc, r) => ({
      input_tokens: acc.input_tokens + (r.cost?.input_tokens || 0),
      output_tokens: acc.output_tokens + (r.cost?.output_tokens || 0),
      total_usd: acc.total_usd + (r.cost?.total_usd || 0),
      elapsed_ms: acc.elapsed_ms + r.elapsedMs,
    }),
    { input_tokens: 0, output_tokens: 0, total_usd: 0, elapsed_ms: 0 },
  );

  console.log("══════════ SUMMARY ══════════");
  console.log(`model           : ${MODEL}`);
  console.log(`calls           : ${results.length}`);
  console.log(`total tokens    : in=${totals.input_tokens}  out=${totals.output_tokens}`);
  console.log(`total cost      : $${totals.total_usd.toFixed(6)}`);
  console.log(`avg cost / call : $${(totals.total_usd / results.length).toFixed(6)}`);
  console.log(`avg latency     : ${(totals.elapsed_ms / results.length).toFixed(0)} ms`);
  console.log(`projected /post : $${(totals.total_usd / results.length).toFixed(6)} per newsfeed post`);
  console.log(`projected /day  : ~$${((totals.total_usd / results.length) * 50).toFixed(4)} at 50 posts/day`);
  console.log(`projected /mo   : ~$${((totals.total_usd / results.length) * 50 * 30).toFixed(2)} at 50 posts/day`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
