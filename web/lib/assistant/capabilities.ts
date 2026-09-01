export const CAPABILITIES = [
  "read",
  "read.spawn",
  "mutate.workspace",
  "mutate.trading",
  "admin",
  "internal",
] as const;

export type Capability = (typeof CAPABILITIES)[number];

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD";

export type RouteCapability = Capability | Partial<Record<HttpMethod, Capability>>;

const REFUSED: ReadonlySet<Capability> = new Set([
  "admin",
  "internal",
  "mutate.trading",
]);

export function isRefused(cap: Capability): boolean {
  return REFUSED.has(cap);
}
