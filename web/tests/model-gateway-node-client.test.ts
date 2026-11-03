import { afterEach, describe, expect, it, vi } from "vitest";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import { completeJson, request } from "../../scripts/model_gateway/client.js";
const closers: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closers.splice(0)) await close(); });
async function serve(handler: http.RequestListener) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "radon-model-test-"));
  const socketPath = path.join(dir, "model.sock");
  const server = http.createServer(handler);
  await new Promise<void>(resolve => server.listen(socketPath, resolve));
  closers.push(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); await fs.rm(dir, { recursive: true, force: true }); });
  return socketPath;
}
describe("credential-free Node gateway transport", () => {
  it("uses a private unix socket with the same JSON wire protocol", async () => {
    let captured: unknown;
    const socketPath = await serve((req, res) => { let body = ""; req.on("data", chunk => body += chunk); req.on("end", () => { captured = { path: req.url, method: req.method, headers: req.headers, body: JSON.parse(body) }; res.end(JSON.stringify({ data: { tags: ["A", "B", "C"] } })); }); });
    await expect(request("json", { instruction: "Read", images: [] }, { socketPath })).resolves.toEqual({ data: { tags: ["A", "B", "C"] } });
    expect(captured).toMatchObject({ path: "/v1/json", method: "POST", body: { instruction: "Read", images: [] } });
    expect((captured as any).headers.authorization).toBeUndefined();
  });
  it("cancels a stuck service request at the total deadline", async () => {
    const socketPath = await serve(() => {});
    await expect(request("json", {}, { socketPath, timeoutMs: 20 })).rejects.toThrow(/deadline/);
  });
  it("continues rejected data with the opaque service token, without picking providers", async () => {
    const requestImpl = vi.fn().mockResolvedValueOnce({ data: { tags: ["A"] }, continuation: "opaque" }).mockResolvedValueOnce({ data: { tags: ["A", "B", "C"] } });
    await expect(completeJson({ instruction: "Tags" }, { requestImpl, accept: (data: any) => data.tags.length === 3 })).resolves.toEqual({ tags: ["A", "B", "C"] });
    expect(requestImpl.mock.calls[1][1]).toEqual({ instruction: "Tags", continuation: "opaque" });
    expect(requestImpl.mock.calls[1][2].timeoutMs).toBeLessThanOrEqual(requestImpl.mock.calls[0][2].timeoutMs);
  });
  it("refuses a repeated or missing continuation", async () => {
    const requestImpl = vi.fn().mockResolvedValue({ data: {}, continuation: "same" });
    await expect(completeJson({}, { requestImpl, accept: () => false })).rejects.toThrow(/acceptable/);
    expect(requestImpl).toHaveBeenCalledTimes(2);
  });
  it("bounds operation names and propagates pre-cancellation", async () => {
    await expect(request("arbitrary", {})).rejects.toThrow(/Unknown/);
    const controller = new AbortController(); controller.abort();
    await expect(request("json", {}, { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
  });
});
it("bounds response bytes and hides raw service errors", async () => {
  const socketPath = await serve((_req, res) => res.end("x".repeat(4 * 1024 * 1024 + 1)));
  await expect(request("json", {}, { socketPath })).rejects.toThrow(/size limit/);
  const refused = await serve((_req, res) => { res.writeHead(503); res.end("secret-provider-diagnostic"); });
  await expect(request("json", {}, { socketPath: refused })).rejects.toThrow("Model gateway HTTP 503");
});
it("rejects malformed service responses and bad local requests", async () => {
  const socketPath = await serve((_req, res) => res.end("not json"));
  await expect(request("json", {}, { socketPath })).rejects.toThrow(/malformed/);
  await expect(request("json", {}, { socketPath: "relative" })).rejects.toThrow(/unavailable/);
  await expect(request("json", {}, { timeoutMs: 0 })).rejects.toThrow(/deadline/);
  await expect(request("json", { data: "x".repeat(16 * 1024 * 1024 + 1) })).rejects.toThrow(/size limit/);
});
it("cancels an in-flight private socket request", async () => {
  const socketPath = await serve(() => {});
  const controller = new AbortController();
  const result = request("json", {}, { socketPath, signal: controller.signal });
  controller.abort();
  await expect(result).rejects.toMatchObject({ name: "AbortError" });
});
