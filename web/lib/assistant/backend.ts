export type BackendMethod = "GET" | "POST";

type AllowRule = {
  method: BackendMethod;
  pattern: RegExp;
};

const ALLOWLIST: AllowRule[] = [
  { method: "GET", pattern: /^\/quote\/[A-Z0-9.\-]+$/ },
  { method: "GET", pattern: /^\/options\/uw-chain$/ },
  { method: "GET", pattern: /^\/options\/chain$/ },
  { method: "GET", pattern: /^\/options\/expirations$/ },
  { method: "GET", pattern: /^\/options\/exposure\/[A-Z0-9.\-]+$/ },
  { method: "GET", pattern: /^\/options\/rv-ratio\/[A-Z0-9.\-]+$/ },
  { method: "GET", pattern: /^\/index-options\/chain$/ },
  { method: "GET", pattern: /^\/catalysts$/ },
  { method: "GET", pattern: /^\/earnings(?:\/[A-Z0-9.\-]+)?$/ },
  { method: "GET", pattern: /^\/informed-flow\/[A-Z0-9.\-]+$/ },
  { method: "GET", pattern: /^\/short-availability\/[A-Z0-9.\-]+$/ },
  { method: "GET", pattern: /^\/ticker\/ratings$/ },
  { method: "GET", pattern: /^\/market-calendar$/ },
  { method: "GET", pattern: /^\/internals\/skew-history$/ },
  { method: "GET", pattern: /^\/cash-flows$/ },
  { method: "GET", pattern: /^\/attribution$/ },
  { method: "GET", pattern: /^\/llm-token-index$/ },
  { method: "GET", pattern: /^\/flow-analysis\/[A-Z0-9.\-]+$/ },
  { method: "POST", pattern: /^\/flow-analysis\/[A-Z0-9.\-]+$/ },
  { method: "POST", pattern: /^\/scan$/ },
  { method: "POST", pattern: /^\/discover$/ },
  { method: "POST", pattern: /^\/gex\/scan$/ },
  { method: "POST", pattern: /^\/vcg\/scan$/ },
  { method: "POST", pattern: /^\/regime\/scan$/ },
  { method: "POST", pattern: /^\/breadth\/scan$/ },
  { method: "POST", pattern: /^\/portfolio\/sync$/ },
  { method: "POST", pattern: /^\/orders\/refresh$/ },
  { method: "POST", pattern: /^\/performance$/ },
  { method: "POST", pattern: /^\/leap\/scan$/ },
  { method: "POST", pattern: /^\/garch-convergence\/scan$/ },
  { method: "POST", pattern: /^\/theta-harvester\/scan$/ },
  { method: "POST", pattern: /^\/strength-confirmation\/scan$/ },
  { method: "POST", pattern: /^\/gamma-rotation\/scan$/ },
  { method: "POST", pattern: /^\/bpi\/scan$/ },
];

const BLOCKED = /\/(orders\/place|orders\/cancel|orders\/modify|trading\/|admin\/|ib\/|paper\/|pi\/exec|knowledge\/)/i;

export function normalizeBackendPath(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed.startsWith("/")) return "";
  const [path] = trimmed.split("?");
  return path.replace(/\/+$/, "") || "/";
}

export function isBackendPathAllowed(method: string, path: string): boolean {
  const verb = method.trim().toUpperCase();
  const normalized = normalizeBackendPath(path);
  if (!normalized || BLOCKED.test(normalized)) return false;
  return ALLOWLIST.some((rule) => rule.method === verb && rule.pattern.test(normalized));
}

export function backendQueryPath(path: string, query?: Record<string, string>): string {
  const normalized = normalizeBackendPath(path);
  if (!query) return normalized;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value) params.set(key, value);
  }
  const encoded = params.toString();
  return encoded ? `${normalized}?${encoded}` : normalized;
}
