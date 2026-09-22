import { authorize, normalizePath } from "@/lib/assistant/catalog";

export type BackendMethod = "GET" | "POST";

export function normalizeBackendPath(raw: string): string {
  return normalizePath(raw);
}

export function isBackendPathAllowed(method: string, path: string): boolean {
  const verb = method.trim().toUpperCase();
  const result = authorize(verb, path);
  if (!result.ok) return false;
  if (result.surface !== "fastapi") return false;
  return result.capability === "read" || result.capability === "read.spawn";
}

export function backendQueryPath(path: string, query?: Record<string, string>): string {
  // The caller passes the path authorize() already normalized. Re-normalizing
  // here would run a second decode pass over it, so require a fixpoint and
  // forward it verbatim instead.
  const normalized = path;
  if (normalizeBackendPath(path) !== path) {
    throw new Error("call_api path is not normalized.");
  }
  if (!query) return normalized;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value) params.set(key, value);
  }
  const encoded = params.toString();
  return encoded ? `${normalized}?${encoded}` : normalized;
}
