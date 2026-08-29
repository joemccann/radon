export const RING_SIZE = 50;
export const MAX_FRAME_BYTES = 64 * 1024;
export const MAX_CONTENT_CHARS = 2_000;
export const MAX_IMPACT = 8;

function clip(text, max) {
  const value = String(text ?? "").replace(/\s+/g, " ").trim();
  if (value.length <= max) return value;
  return value.slice(0, max);
}

function impacts(raw) {
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const row of raw.slice(0, MAX_IMPACT)) {
    if (!row || typeof row !== "object") continue;
    const symbol = clip(row.symbol, 16);
    const impact = clip(row.impact, 16);
    if (!symbol && !impact) continue;
    out.push({ symbol, impact });
  }
  return out;
}

export function toHeadline(msg) {
  if (!msg || msg.kind === "time" || msg.kind === "raw") return null;
  const row = msg.kind === "flash" || msg.kind === "headline" ? msg.data : null;
  if (!row || typeof row !== "object" || Array.isArray(row)) return null;
  const inner = row.data && typeof row.data === "object" ? row.data : {};
  const content = clip(inner.content || inner.title || "", MAX_CONTENT_CHARS);
  const id = clip(row.id, 80);
  if (!id || !content) return null;
  return {
    kind: "headline",
    id,
    time: typeof row.time === "string" ? row.time : null,
    important: Boolean(row.important),
    content,
    impact: impacts(row.impact),
  };
}

export function containsUpstreamHost(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return /mktnews\.net/i.test(text);
}
