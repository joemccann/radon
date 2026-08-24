/**
 * Radon FastAPI client — minimal fetch helper for Next.js routes.
 *
 * All POST operations go through FastAPI.
 * Attaches Clerk JWT when available for authenticated requests.
 */

const RADON_API = process.env.RADON_API_URL || "http://localhost:8321";

/**
 * A FastAPI error detail. Usually a string; a structured detail that carries
 * a `code` is preserved as an object so route handlers can branch on it.
 */
export type RadonErrorDetail = string | Record<string, unknown>;

export class RadonApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: RadonErrorDetail,
  ) {
    super(
      `Radon API ${status}: ${
        typeof detail === "string" ? detail : JSON.stringify(detail)
      }`,
    );
    this.name = "RadonApiError";
  }
}

export function coerceRadonErrorDetail(
  body: unknown,
  status: number,
): RadonErrorDetail {
  if (typeof body === "string") return body;
  if (body === null || body === undefined) return `HTTP ${status}`;
  if (typeof body !== "object") return String(body);
  const obj = body as Record<string, unknown>;
  const raw = obj.detail ?? obj.error ?? obj.message;
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object") {
    const nested = raw as Record<string, unknown>;
    // R-090: a coded detail is the ONLY thing that reaches the
    // REPLACE_PARTIAL / REPLACE_INDETERMINATE renderer, and flattening it to
    // JSON.stringify made that branch unreachable — the operator never saw
    // "the working orders were already cancelled at IB", the sentence that
    // stops a blind retry from double-placing an unhedged position.
    if (typeof nested.code === "string") return nested;
    if (typeof nested.detail === "string") return nested.detail;
    if (typeof nested.message === "string") return nested.message;
    if (typeof nested.error === "string") return nested.error;
    return JSON.stringify(raw);
  }
  return JSON.stringify(body);
}

/** Flatten a detail for callers that render a plain string. */
export function radonErrorDetailText(detail: RadonErrorDetail): string {
  return typeof detail === "string" ? detail : JSON.stringify(detail);
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
    let detail: RadonErrorDetail;
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
