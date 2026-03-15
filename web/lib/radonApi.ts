/**
 * Radon FastAPI client — minimal fetch helper for Next.js routes.
 *
 * All POST operations go through FastAPI on localhost:8321.
 * No spawn fallback — on failure, callers serve cached data from disk.
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

export async function radonFetch<T = Record<string, unknown>>(
  path: string,
  opts?: RequestInit & { timeout?: number },
): Promise<T> {
  const { timeout = 30_000, ...fetchOpts } = opts ?? {};
  const res = await fetch(`${RADON_API}${path}`, {
    ...fetchOpts,
    signal: AbortSignal.timeout(timeout),
  });
  if (!res.ok) {
    let detail: string;
    try {
      const body = await res.json();
      detail = body.detail ?? body.error ?? JSON.stringify(body);
    } catch {
      detail = await res.text().catch(() => `HTTP ${res.status}`);
    }
    throw new RadonApiError(res.status, detail);
  }
  return res.json();
}
