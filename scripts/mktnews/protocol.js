export const DEFAULT_HOST = "wss://api.mktnews.net/";
export const DEFAULT_LANG = "en";
export const DEFAULT_URL = `${DEFAULT_HOST}?lang=${DEFAULT_LANG}`;
export const DEFAULT_ORIGIN = "https://mktnews.net";
export const DEFAULT_USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

function decodeFrame(raw) {
  if (Buffer.isBuffer(raw)) return raw.toString("utf8");
  if (raw instanceof ArrayBuffer) return Buffer.from(raw).toString("utf8");
  if (ArrayBuffer.isView(raw)) return Buffer.from(raw.buffer, raw.byteOffset, raw.byteLength).toString("utf8");
  return String(raw);
}

function isFlashItem(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const inner = value.data;
  if (!value.id || !inner || typeof inner !== "object") return false;
  return "content" in inner || "title" in inner;
}

export function classifyMessage(msg) {
  if (!msg || typeof msg !== "object") return "raw";
  if (msg.kind) return msg.kind;
  const value = msg.data ?? msg;
  if (value && typeof value === "object" && value.type === "time" && typeof value.data === "number") {
    return "time";
  }
  if (typeof msg.type === "string") return msg.type;
  if (isFlashItem(value)) return "flash";
  return "unknown";
}

export function parseFrame(raw) {
  const text = decodeFrame(raw);
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    return { kind: "raw", type: null, data: text, raw: text };
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    if (value.type === "time" && typeof value.data === "number") {
      return { kind: "time", type: "time", data: value.data, raw: text };
    }
    if (typeof value.type === "string") {
      return { kind: value.type, type: value.type, data: value.data ?? value, raw: text };
    }
    if (isFlashItem(value)) {
      return { kind: "flash", type: value.type ?? 0, data: value, raw: text };
    }
  }
  return { kind: "unknown", type: value?.type ?? null, data: value, raw: text };
}
