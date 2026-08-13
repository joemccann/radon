/**
 * Radon FastAPI client — minimal fetch helper for Next.js routes.
 *
 * All POST operations go through FastAPI.
 * Attaches Clerk JWT when available for authenticated requests.
 */

const RADON_API = process.env.RADON_API_URL || "http://localhost:8321";

export class RadonApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`Radon API ${status}: ${detail}`);
    this.name = "RadonApiError";
  }
}

export function coerceRadonErrorDetail(body: unknown, status: number): string {
  if (typeof body === "string") return body;
  if (body === null || body === undefined) return `HTTP ${status}`;
  if (typeof body !== "object") return String(body);
  const obj = body as Record<string, unknown>;
  const raw = obj.detail ?? obj.error ?? obj.message;
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object") {
    const nested = raw as Record<string, unknown>;
    if (typeof nested.detail === "string") return nested.detail;
    if (typeof nested.message === "string") return nested.message;
    if (typeof nested.error === "string") return nested.error;
    return JSON.stringify(raw);
  }
  return JSON.stringify(body);
}

export async function radonFetch<T = Record<string, unknown>>(
  path: string,
  opts?: RequestInit & { timeout?: number; token?: string },
): Promise<T> {
  const { timeout = 30_000, token, ...fetchOpts } = opts ?? {};
  const headers = new Headers(fetchOpts.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  // Demo topology: the frontend (Vercel) and the demo VM FastAPI are not on a
  // shared loopback/tailnet, so the backend can't trust us by origin. We
  // authenticate as a trusted service with the shared token instead of
  // forwarding a per-user JWT. Unset on prod (loopback-trusted) -> no header.
  const serviceToken = process.env.RADON_SERVICE_TOKEN;
  if (serviceToken) {
    headers.set("X-Radon-Service-Token", serviceToken);
  }
  const timeoutSignal = AbortSignal.timeout(timeout);
  const signal = fetchOpts.signal
    ? AbortSignal.any([fetchOpts.signal, timeoutSignal])
    : timeoutSignal;
  const res = await fetch(`${RADON_API}${path}`, {
    ...fetchOpts,
    headers,
    cache: fetchOpts.cache ?? "no-store",
    signal,
  });
  if (!res.ok) {
    let detail: string;
    try {
      const body = await res.json();
      detail = coerceRadonErrorDetail(body, res.status);
    } catch {
      detail = await res.text().catch(() => `HTTP ${res.status}`);
    }
    throw new RadonApiError(res.status, detail);
  }
  return res.json();
}

export async function radonFetchText(
  path: string,
  opts?: RequestInit & { timeout?: number; token?: string },
): Promise<string> {
  const { timeout = 30_000, token, ...fetchOpts } = opts ?? {};
  const headers = new Headers(fetchOpts.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const serviceToken = process.env.RADON_SERVICE_TOKEN;
  if (serviceToken) headers.set("X-Radon-Service-Token", serviceToken);
  const timeoutSignal = AbortSignal.timeout(timeout);
  const signal = fetchOpts.signal ? AbortSignal.any([fetchOpts.signal, timeoutSignal]) : timeoutSignal;
  const res = await fetch(`${RADON_API}${path}`, {
    ...fetchOpts,
    headers,
    cache: fetchOpts.cache ?? "no-store",
    signal,
  });
  if (!res.ok) throw new RadonApiError(res.status, await res.text().catch(() => `HTTP ${res.status}`));
  return res.text();
}
