import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "./apiContracts";
import { resolveDemoContext, type DemoPublicMetadata } from "./demo/demoContext";
import { rateLimit, type RateLimitResult } from "./rateLimit";
import { demoRateLimit, type DemoRateLimitResult, type DemoRateTier } from "./demo/rateLimit";

type AuthResult = {
  userId?: string | null;
  sessionClaims?: { metadata?: DemoPublicMetadata } | null;
  publicMetadata?: DemoPublicMetadata | null;
  getToken?: () => Promise<string | null>;
};

type Env = Partial<Record<
  "ALLOWED_USER_IDS" | "RADON_REQUIRE_OPERATOR_ALLOWLIST" | "NEXT_PUBLIC_RADON_DEMO" | "NODE_ENV",
  string | undefined
>>;

export type RoutePrincipal = {
  userId: string;
  kind: "operator" | "demo" | "authenticated" | "test";
  token?: string;
};

export type RouteAccessOptions = {
  operatorOnly?: boolean;
  rate?: { key: string; limit: number; windowMs: number };
  durableRateTier?: DemoRateTier;
};

export type RouteAccessDeps = {
  authFn?: () => Promise<AuthResult>;
  env?: Env;
  now?: number;
  rateLimitFn?: typeof rateLimit;
  durableRateLimitFn?: (tier: DemoRateTier, key: string) => Promise<DemoRateLimitResult>;
};

export type RouteAccessResult =
  | { ok: true; principal: RoutePrincipal }
  | { ok: false; response: Response };

function parseAllowed(raw: string | undefined): Set<string> {
  return new Set((raw ?? "").split(",").map((value) => value.trim()).filter(Boolean));
}

async function defaultAuth(): Promise<AuthResult> {
  const { auth } = await import("@clerk/nextjs/server");
  return (await auth()) as unknown as AuthResult;
}

function reject(status: 401 | 403 | 429 | 503, message: string, retryAfter?: number): RouteAccessResult {
  const requestId = getRequestId();
  const response = jsonApiError({
    message,
    status,
    code: status === 401
      ? "UNAUTHORIZED"
      : status === 403
        ? "FORBIDDEN"
        : status === 429
          ? "RATE_LIMITED"
          : "CONFIG_ERROR",
    requestId,
  });
  if (retryAfter !== undefined) response.headers.set("Retry-After", String(retryAfter));
  return { ok: false, response: setNoStoreResponseHeaders(response, requestId) };
}

/**
 * Handler-local identity, deployment capability, and optional abuse gate.
 * Middleware remains a first perimeter; this guard prevents route exports from
 * becoming trusted-service deputies when mounted, rewritten, or called directly.
 */
export async function requireRouteAccess(
  request?: Request,
  options: RouteAccessOptions = {},
  deps: RouteAccessDeps = {},
): Promise<RouteAccessResult> {
  const env: Env = deps.env ?? process.env;
  let authResult: AuthResult;
  try {
    authResult = await (deps.authFn ?? defaultAuth)();
  } catch {
    // Direct unit tests run route exports without a Clerk request scope. This
    // seam is compile-time test-only and cannot be enabled by a public env flag.
    if (env.NODE_ENV === "test") {
      return { ok: true, principal: { userId: "test", kind: "test" } };
    }
    return reject(401, "Unauthorized");
  }

  const userId = authResult.userId ?? null;
  if (!userId) {
    return reject(401, "Unauthorized");
  }

  const allowed = parseAllowed(env.ALLOWED_USER_IDS);
  const allowlisted = allowed.has(userId);
  if (options.operatorOnly && !allowlisted) return reject(403, "Forbidden");
  if (allowed.size > 0 && !allowlisted) return reject(403, "Forbidden");
  if (env.RADON_REQUIRE_OPERATOR_ALLOWLIST === "1" && allowed.size === 0) {
    return reject(403, "Forbidden");
  }

  let kind: RoutePrincipal["kind"] = allowlisted ? "operator" : "authenticated";
  if (env.NEXT_PUBLIC_RADON_DEMO === "1" && !allowlisted) {
    const metadata = authResult.sessionClaims?.metadata ?? authResult.publicMetadata ?? null;
    const demo = resolveDemoContext(metadata, deps.now ?? Date.now());
    if (!demo || demo.expired) return reject(403, "Demo access is not active");
    kind = "demo";
  }

  if (options.rate) {
    const limited = (deps.rateLimitFn ?? rateLimit)(
      `route:${options.rate.key}:${userId}`,
      { limit: options.rate.limit, windowMs: options.rate.windowMs },
    ) as RateLimitResult;
    if (!limited.ok) return reject(429, "Too Many Requests", limited.retryAfterSec);
  }

  if (options.rate && (env.NODE_ENV === "production" || env.NEXT_PUBLIC_RADON_DEMO === "1" || deps.durableRateLimitFn)) {
    try {
      const tier = options.durableRateTier ?? "B";
      const durable = await (deps.durableRateLimitFn ?? demoRateLimit)(
        tier,
        `route:${options.rate.key}:${userId}`,
      );
      if (!durable.success) {
        const retryAfter = durable.reset > 0
          ? Math.max(0, Math.ceil((durable.reset - (deps.now ?? Date.now())) / 1_000))
          : undefined;
        return reject(429, "Too Many Requests", retryAfter);
      }
    } catch {
      return reject(503, "Rate limit service unavailable");
    }
  }

  const token = authResult.getToken ? (await authResult.getToken()) ?? undefined : undefined;
  return { ok: true, principal: { userId, kind, ...(token ? { token } : {}) } };
}
