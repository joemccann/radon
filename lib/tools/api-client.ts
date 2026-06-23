/**
 * Minimal FastAPI client for root tool wrappers.
 */

const RADON_API = process.env.RADON_API_URL || "http://localhost:8321";

export async function postRadonJson(path: string, timeout = 30_000): Promise<unknown> {
  const res = await fetch(`${RADON_API}${path}`, {
    method: "POST",
    cache: "no-store",
    signal: AbortSignal.timeout(timeout),
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object") {
        const raw = (body as Record<string, unknown>).detail
          ?? (body as Record<string, unknown>).error
          ?? (body as Record<string, unknown>).message;
        detail = typeof raw === "string" ? raw : JSON.stringify(body);
      } else {
        detail = String(body);
      }
    } catch {
      detail = await res.text().catch(() => detail);
    }
    throw new Error(detail);
  }

  return res.json();
}
