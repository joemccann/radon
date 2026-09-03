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
  "ALLOWED_USER_IDS" | "RADON_REQUIRE_OPERATOR_ALLOWLIST" | "NEXT_PUBLIC_RADON_DEMO",
  string | undefined
>>;

export type RoutePrincipal = {
  userId: string;
  kind: "operator" | "demo" | "authenticated" | "test";
  token?: string;
};

export type RouteAccessOptions = {
  operatorOnly?: boolean;
  /**
   * The route carries its own demo order blockade downstream (paper path or an
   * explicit per-action refusal — lib/demo/orderBlockade.ts). On the demo
   * deployment (NEXT_PUBLIC_RADON_DEMO=1, ALLOWED_USER_IDS absent by design)
   * an ACTIVE demo principal passes operatorOnly so the blockade can route it;
   * everywhere else operatorOnly stays fail-closed.
   */
  demoBlockadeRoute?: boolean;
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

// One dynamic import per module instance. Two handlers racing separate
// `import()` calls of a vi.mock'd module hand the loser the real Clerk
// module, which throws outside a request scope and lands the caller in the
// NODE_ENV=test seam below as a different user. A rejected import is not
// cached so the next call retries.
let clerkServer: Promise<typeof import("@clerk/nextjs/server")> | undefined;

async function defaultAuth(): Promise<AuthResult> {
  clerkServer ??= import("@clerk/nextjs/server").catch((error) => {
    clerkServer = undefined;
    throw error;
  });
  const { auth } = await clerkServer;
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
  const env: Env = deps.env ?? (process.env as Env);
  let authResult: AuthResult;
  try {
    authResult = await (deps.authFn ?? defaultAuth)();
  } catch {
    // Direct unit tests run route exports without a Clerk request scope. The
    // seam keys on the `process.env.NODE_ENV` LITERAL — inlined at build time
    // by the bundler and dead-code-eliminated from production output — never
    // on the runtime `deps.env` object, so no injected env can open it.
    if (process.env.NODE_ENV === "test") {
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

  // Demo identity resolves BEFORE the operatorOnly gate so a demo-blockade
  // route can admit an active demo principal; an inactive/expired one is
  // rejected here regardless of the route's options.
  let demoActive = false;
  if (env.NEXT_PUBLIC_RADON_DEMO === "1" && !allowlisted) {
    const metadata = authResult.sessionClaims?.metadata ?? authResult.publicMetadata ?? null;
    const demo = resolveDemoContext(metadata, deps.now ?? Date.now());
    if (!demo || demo.expired) return reject(403, "Demo access is not active");
    demoActive = true;
  }

  const admittedByDemoBlockade = demoActive && options.demoBlockadeRoute === true;
  if (options.operatorOnly && !allowlisted && !admittedByDemoBlockade) {
    return reject(403, "Forbidden");
  }
  // R-182: this line ignored `admittedByDemoBlockade` entirely, so the
  // admission two lines above was undone whenever ALLOWED_USER_IDS was
  // non-empty — the escape hatch existed only on a deployment with no
  // allowlist at all, which is not what the option documents.
  if (allowed.size > 0 && !allowlisted && !admittedByDemoBlockade) {
    return reject(403, "Forbidden");
  }
  if (env.RADON_REQUIRE_OPERATOR_ALLOWLIST === "1" && allowed.size === 0) {
    return reject(403, "Forbidden");
  }

  const kind: RoutePrincipal["kind"] = allowlisted
    ? "operator"
    : demoActive
      ? "demo"
      : "authenticated";

  if (options.rate) {
    const limited = (deps.rateLimitFn ?? rateLimit)(
      `route:${options.rate.key}:${userId}`,
      { limit: options.rate.limit, windowMs: options.rate.windowMs },
    ) as RateLimitResult;
    if (!limited.ok) return reject(429, "Too Many Requests", limited.retryAfterSec);
  }

  // Upstash is an isolated demo-deployment dependency and must never be
  // configured in the operator deployment (docs/demo-environment.md). The
  // operator remains protected by the allowlist, the per-principal worker
  // budget above, and backend admission/single-flight controls. Demo traffic
  // additionally consumes its fail-closed cross-instance spend/DOS budget.
  if (options.rate && kind === "demo") {
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
