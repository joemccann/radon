/** Credential-free Node transport to the private standalone model service. */
import http from "node:http";

const OPERATIONS = new Set(["chat", "json", "models", "status"]);
const MAX_REQUEST_BYTES = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;

export function request(operation, payload, { timeoutMs = 30_000, signal, socketPath = process.env.RADON_MODEL_GATEWAY_SOCKET || "/run/radon-model-gateway/gateway.sock" } = {}) {
  if (!OPERATIONS.has(operation)) return Promise.reject(new Error("Unknown model gateway operation"));
  if (signal?.aborted) return Promise.reject(signal.reason ?? new Error("Model gateway request aborted"));
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 1_800_000) return Promise.reject(new Error("Invalid model gateway deadline"));
  if (typeof socketPath !== "string" || !socketPath.startsWith("/") || socketPath.includes("\0") || socketPath.length > 100) return Promise.reject(new Error("Model gateway unavailable"));
  const body = JSON.stringify(payload ?? {});
  if (body && Buffer.byteLength(body) > MAX_REQUEST_BYTES) return Promise.reject(new Error("Model gateway request exceeds size limit"));
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      if (error) reject(error); else resolve(value);
    };
    const req = http.request({ socketPath, path: `/v1/${operation}`, method: "POST", headers: { "content-type": "application/json", ...(body ? { "content-length": Buffer.byteLength(body) } : {}) } }, res => {
      const chunks = [];
      let size = 0;
      res.on("data", chunk => {
        size += chunk.length;
        if (size > MAX_RESPONSE_BYTES) { finish(new Error("Model gateway response exceeds size limit")); res.destroy(); req.destroy(); return; }
        chunks.push(chunk);
      });
      res.on("error", () => finish(new Error("Model gateway response failed")));
      res.on("end", () => {
        if (res.statusCode < 200 || res.statusCode >= 300) { finish(new Error(`Model gateway HTTP ${res.statusCode}`)); return; }
        try { finish(null, JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
        catch { finish(new Error("Model gateway response is malformed")); }
      });
    });
    const abort = () => { finish(signal.reason ?? new Error("Model gateway request aborted")); req.destroy(); };
    req.on("error", () => finish(new Error("Model gateway unavailable")));
    signal?.addEventListener("abort", abort, { once: true });
    timer = setTimeout(() => { finish(new Error("Model gateway deadline exceeded")); req.destroy(); }, timeoutMs);
    req.end(body);
  });
}

/** Continue only with a service-issued traversal token; callers never walk providers. */
export async function completeJson(payload, { timeoutMs = 30_000, signal, accept = () => true, onResult, requestImpl = request, ...transport } = {}) {
  const deadline = Date.now() + timeoutMs;
  let continuation;
  const seen = new Set();
  while (Date.now() < deadline) {
    const result = await requestImpl("json", { ...payload, ...(continuation ? { continuation } : {}) }, { ...transport, timeoutMs: Math.max(1, deadline - Date.now()), signal });
    if (!result || typeof result !== "object" || !("data" in result)) throw new Error("Model gateway JSON result is malformed");
    if (accept(result.data)) {
      if (onResult) await onResult(result);
      return result.data;
    }
    if (typeof result.continuation !== "string" || !result.continuation || seen.has(result.continuation)) throw new Error("Model gateway returned no acceptable result");
    continuation = result.continuation;
    seen.add(continuation);
  }
  throw new Error("Model gateway deadline exceeded");
}
