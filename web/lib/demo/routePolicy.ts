// What a demo principal gets from every API route on demo.radon.run.
//
// Each route on disk is a decision, not a default: a new route ships into a
// deployment with no IB, a separate Turso fed only by the mirrors and the
// seed, and a TEST_MODE backend. Without a forced decision, features shipped
// "working" on the operator app and rendered empty panels or raw errors on
// demo. Drift pin: web/tests/demo-route-policy.test.ts walks app/api and fails
// on any route or exported method missing here, any stale entry, a fixture
// route without a demo branch, a blocked route without its guard, and any
// demo Turso table nothing in the demo environment produces.
//
// Keys are route-file paths ("/api/..." with [param] segments); values map
// each exported HTTP method to its demo decision.

export type DemoHttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** How a "blocked" route refuses a demo principal. */
export type DemoBlockMechanism =
  /** requireRouteAccess({ operatorOnly: true }) without demoBlockadeRoute. */
  | "operatorOnly"
  /** requireDemoAdmin (DEMO_ADMIN_USER_IDS allowlist). */
  | "demoAdmin"
  /** First-run setup wizard: hard 404 whenever Clerk keys are configured. */
  | "setupMode"
  /** demoBlockadeRoute admission, then an explicit demoOrderRefusal. */
  | "orderBlockade";

type Tables = {
  /** demo Turso tables read on the demo path. */
  tables?: readonly string[];
  /** demo Turso tables written on the demo path. */
  writes?: readonly string[];
};

export type DemoMethodPolicy =
  /** A demo-principal branch serves a request-time sample. */
  | {
      mode: "fixture";
      /** The demo branch is being added in a separate change; until it lands
       * the route still runs its operator path for demo. Drop the flag when
       * the branch ships (the drift pin fails until you do). */
      pendingFixture?: true;
      note?: string;
    }
  /** Reads (or writes) the demo Turso. */
  | (Tables & { mode: "demo-db"; endpoints?: readonly string[]; note?: string })
  /** Proxies the demo VM FastAPI (RADON_API_TEST_MODE=1). */
  | (Tables & { mode: "backend"; endpoints: readonly string[]; note?: string })
  /** Demo principal is refused. */
  | { mode: "blocked"; via: DemoBlockMechanism }
  /** Reaches an LLM provider; metered by the shared tier-D AI budget. */
  | (Tables & { mode: "ai"; note?: string })
  /** Calls a third-party API directly from Next.js. `sources` are host or
   * path substrings present in the route's import graph. */
  | (Tables & { mode: "external"; sources: readonly string[]; note?: string })
  /** Reachable signed out (share previews, webhooks, bearer probes). */
  | (Tables & { mode: "public"; endpoints?: readonly string[]; note: string })
  /** Build, version, config or token metadata. */
  | (Tables & { mode: "infra"; note: string })
  /** Known gap: renders nothing on demo. */
  | { mode: "unavailable"; note: string };

export type DemoRoutePolicy = Readonly<Partial<Record<DemoHttpMethod, DemoMethodPolicy>>>;

const OPERATOR: DemoMethodPolicy = { mode: "blocked", via: "operatorOnly" };
const SCAN_SNAPSHOTS: DemoMethodPolicy = { mode: "demo-db", tables: ["scan_snapshots"] };
const YAHOO = "query1.finance.yahoo.com";

export const DEMO_ROUTE_POLICY: Readonly<Record<string, DemoRoutePolicy>> = {
  // ── operator administration ─────────────────────────────────────────────
  "/api/admin/demo-users": {
    GET: { mode: "blocked", via: "demoAdmin" },
    POST: { mode: "blocked", via: "demoAdmin" },
  },
  "/api/admin/edge-health": { GET: OPERATOR },
  "/api/admin/health": { GET: OPERATOR },
  "/api/admin/host-metrics": { GET: OPERATOR },
  "/api/admin/ib/operator-hold": { GET: OPERATOR, POST: OPERATOR },
  "/api/admin/ib/reset-backoff": { POST: OPERATOR },
  "/api/admin/ib/restart": { POST: OPERATOR },
  "/api/admin/logs": { GET: OPERATOR },
  "/api/admin/reliability": { GET: OPERATOR },
  "/api/admin/services": { GET: OPERATOR },
  "/api/admin/services/[unit]/[action]": { POST: OPERATOR },
  "/api/admin/slm-review": { GET: OPERATOR, PUT: OPERATOR },
  "/api/admin/slo": { GET: OPERATOR },
  "/api/admin/stack/restart": { POST: OPERATOR },
  "/api/admin/trading/[action]": { GET: OPERATOR, POST: OPERATOR },
  "/api/credentials": { GET: OPERATOR },
  "/api/credentials/[service]": { PUT: OPERATOR, DELETE: OPERATOR },
  "/api/ib/ws-ticket": { POST: OPERATOR },
  "/api/research/evidence/[asset]": { GET: OPERATOR },
  "/api/research/governance": { GET: OPERATOR },
  "/api/newsfeed/research/feedback": { POST: OPERATOR },
  "/api/newsfeed/research/files/[asset]": { GET: OPERATOR },
  "/api/newsfeed/research/held": { GET: OPERATOR },
  "/api/newsfeed/research/rules": { GET: OPERATOR, POST: OPERATOR },
  "/api/newsfeed/share": { POST: OPERATOR },
  "/api/setup/complete": { POST: { mode: "blocked", via: "setupMode" } },
  "/api/setup/status": { POST: { mode: "blocked", via: "setupMode" } },
  "/api/setup/validate": { POST: { mode: "blocked", via: "setupMode" } },

  // ── account: seeded book, request-time samples, paper orders ────────────
  "/api/blotter": { GET: { mode: "demo-db", tables: ["journal"] }, POST: OPERATOR },
  "/api/cash-flows": { GET: { mode: "fixture" } },
  "/api/flow-analysis": {
    GET: { mode: "demo-db", tables: ["flow_analysis_snapshots"] },
    POST: OPERATOR,
  },
  "/api/journal": { GET: { mode: "demo-db", tables: ["journal"] }, POST: OPERATOR },
  "/api/journal/sync": { POST: OPERATOR },
  "/api/orders": {
    GET: {
      mode: "demo-db",
      tables: ["open_orders", "executed_orders"],
      note: "Seeded open orders plus request-time fixture executions (buildDemoOrders).",
    },
    POST: OPERATOR,
  },
  "/api/orders/cancel": { POST: { mode: "blocked", via: "orderBlockade" } },
  "/api/orders/modify": { POST: { mode: "blocked", via: "orderBlockade" } },
  "/api/orders/place": {
    POST: {
      mode: "backend",
      endpoints: ["/paper/place"],
      tables: ["open_orders", "executed_orders"],
      note: "resolveDemoOrderDecision routes the order to the paper path (paper_fills).",
    },
  },
  "/api/orders/whatif": {
    POST: { mode: "fixture", note: "Static margin-unavailable envelope via resolveDemoOrderDecision." },
  },
  "/api/paper/place": { POST: { mode: "backend", endpoints: ["/paper/place"] } },
  "/api/performance": { GET: { mode: "fixture" }, POST: OPERATOR },
  "/api/pi": { POST: OPERATOR },
  "/api/portfolio": { GET: { mode: "demo-db", tables: ["portfolio_snapshots", "journal"] }, POST: OPERATOR },
  "/api/preferences": {
    GET: { mode: "backend", endpoints: ["/preferences"] },
    PUT: OPERATOR,
    DELETE: OPERATOR,
  },

  // ── per-user stores written by the demo user ─────────────────────────────
  "/api/alerts": {
    GET: { mode: "demo-db", tables: ["alert_rules"] },
    POST: { mode: "demo-db", tables: ["alert_rules"], writes: ["alert_rules"] },
  },
  "/api/alerts/[id]": { DELETE: { mode: "demo-db", writes: ["alert_rules"] } },
  "/api/bookmarks": {
    GET: { mode: "demo-db", tables: ["bookmarks"] },
    POST: { mode: "demo-db", writes: ["bookmarks"] },
  },
  "/api/bookmarks/[post_id]": { DELETE: { mode: "demo-db", writes: ["bookmarks"] } },
  "/api/profile": {
    GET: { mode: "demo-db", tables: ["user_profiles"] },
    PUT: { mode: "demo-db", tables: ["user_profiles"], writes: ["user_profiles"] },
  },
  "/api/watchlist": {
    GET: { mode: "demo-db", tables: ["user_watchlist"] },
    POST: { mode: "demo-db", writes: ["user_watchlist"] },
  },
  "/api/watchlist/[symbol]": { DELETE: { mode: "demo-db", writes: ["user_watchlist"] } },

  // ── LLM ──────────────────────────────────────────────────────────────────
  "/api/assistant": {
    POST: {
      mode: "ai",
      tables: ["demo_ai_usage"],
      writes: ["demo_ai_usage", "assistant_turns"],
      note: "Tool calls run inside runWithDemoDbPrincipal.",
    },
  },
  "/api/newsfeed/explain": {
    POST: {
      mode: "ai",
      tables: ["demo_ai_usage", "newsfeed_explanations"],
      writes: ["demo_ai_usage", "newsfeed_explanations"],
    },
  },

  // ── request-time regime / options samples ────────────────────────────────
  "/api/bpi": { GET: { mode: "fixture" } },
  "/api/dispersion": { GET: { mode: "fixture" } },
  "/api/flow-analysis/[ticker]": { GET: { mode: "fixture" }, POST: { mode: "fixture" } },
  "/api/gamma-rotation": { GET: { mode: "fixture" }, POST: { mode: "fixture" } },
  "/api/gex": { GET: { mode: "fixture" }, POST: { mode: "fixture" } },
  "/api/options/chain": { GET: { mode: "fixture" } },
  "/api/options/expirations": { GET: { mode: "fixture" } },
  "/api/options/exposure": { GET: { mode: "fixture" } },
  "/api/regime": { GET: { mode: "fixture" }, POST: { mode: "fixture" } },
  "/api/scanner/theta": { GET: { mode: "fixture" } },
  "/api/scanner/theta/scan": { POST: { mode: "fixture" } },
  "/api/trin": { GET: { mode: "fixture" } },
  "/api/vcg": { GET: { mode: "fixture" } },

  // Demo branches landing in a concurrent change (pendingFixture).
  "/api/ai-cycle": {
    GET: { mode: "fixture", pendingFixture: true, note: "Today: backend /ai-cycle." },
  },
  "/api/breadth": {
    GET: { mode: "fixture", pendingFixture: true, note: "Today: demo-db breadth_snapshots." },
    POST: { mode: "backend", endpoints: ["/breadth/scan"], tables: ["breadth_snapshots"] },
  },
  "/api/catalysts": {
    GET: {
      mode: "fixture",
      pendingFixture: true,
      note: "Today: demo-db catalysts, which no mirror fills.",
    },
  },
  "/api/ticker/info": {
    GET: {
      mode: "fixture",
      pendingFixture: true,
      note: "Today: Unusual Whales + Exa + Yahoo under the tier-D quota.",
    },
  },
  "/api/ticker/seasonality": {
    GET: {
      mode: "fixture",
      pendingFixture: true,
      note: "Today: Unusual Whales, then an Anthropic vision fallback, under the tier-D quota.",
    },
  },

  // ── mirrored market snapshots (scripts/db/mirror_market_snapshots_to_demo.js)
  "/api/calm-streak": { GET: SCAN_SNAPSHOTS },
  "/api/cor": { GET: SCAN_SNAPSHOTS },
  "/api/credit-spread": { GET: SCAN_SNAPSHOTS },
  "/api/credit-vix": { GET: SCAN_SNAPSHOTS },
  "/api/discover": {
    GET: { mode: "demo-db", tables: ["discover_snapshots"] },
    POST: { mode: "backend", endpoints: ["/discover"] },
  },
  "/api/divyield": { GET: SCAN_SNAPSHOTS },
  "/api/equibles-ats-venue-share": { GET: SCAN_SNAPSHOTS },
  "/api/equibles-cot-positioning": { GET: SCAN_SNAPSHOTS },
  "/api/equibles-filing-forensics": { GET: { mode: "demo-db", tables: ["equibles_filing_forensics"] } },
  "/api/equibles-short-crowding": { GET: SCAN_SNAPSHOTS },
  "/api/equibles-smart-money-13f": { GET: { mode: "demo-db", tables: ["equibles_13f_snapshots"] } },
  "/api/flow-surprise": { GET: SCAN_SNAPSHOTS },
  "/api/garch-convergence": { GET: SCAN_SNAPSHOTS },
  "/api/hhlev": { GET: SCAN_SNAPSHOTS },
  "/api/hyad": { GET: SCAN_SNAPSHOTS },
  "/api/iei-hyg": { GET: SCAN_SNAPSHOTS },
  "/api/internals": {
    GET: {
      mode: "demo-db",
      tables: ["cri_snapshots", "menthorq_cta"],
      endpoints: ["/internals/skew-history", "/regime/scan"],
      note: "Skew history always comes from the backend on Vercel (no disk cache).",
    },
    POST: {
      mode: "backend",
      endpoints: ["/regime/scan", "/internals/skew-history"],
      tables: ["menthorq_cta"],
    },
  },
  "/api/iv-spread": { GET: SCAN_SNAPSHOTS },
  "/api/ivrank": { GET: SCAN_SNAPSHOTS },
  "/api/leap": { GET: SCAN_SNAPSHOTS },
  "/api/ma-ratio": { GET: SCAN_SNAPSHOTS },
  "/api/margin-debt": { GET: SCAN_SNAPSHOTS },
  "/api/menthorq/cta": { GET: { mode: "demo-db", tables: ["menthorq_cta"] } },
  "/api/newsfeed/posts": {
    GET: { mode: "demo-db", tables: ["posts", "research_post_sources", "research_feedback"] },
  },
  "/api/options/rv-ratio": {
    GET: { mode: "demo-db", tables: ["rv_ratio_snapshots"] },
    POST: { mode: "backend", endpoints: ["/options/rv-ratio/"] },
  },
  "/api/panic-index": { GET: SCAN_SNAPSHOTS },
  "/api/record-high-breadth": { GET: SCAN_SNAPSHOTS },
  "/api/rsi-oversold": { GET: SCAN_SNAPSHOTS },
  "/api/scanner": {
    GET: { mode: "demo-db", tables: ["scanner_snapshots"] },
    POST: { mode: "backend", endpoints: ["/scan"] },
  },
  "/api/scanner/bounce": { GET: SCAN_SNAPSHOTS },
  "/api/scanner/strength": { GET: { mode: "demo-db", tables: ["strength_confirmation_snapshots"] } },
  "/api/scanner/vol-skew-mr": { GET: { mode: "demo-db", tables: ["vol_skew_mr_snapshots"] } },
  "/api/service-health": { GET: { mode: "demo-db", tables: ["service_health"] } },
  "/api/skew": { GET: SCAN_SNAPSHOTS },
  "/api/skew2d": { GET: SCAN_SNAPSHOTS },
  "/api/straddle": { GET: SCAN_SNAPSHOTS },
  "/api/vix-pc": { GET: SCAN_SNAPSHOTS },
  "/api/vixcor": { GET: SCAN_SNAPSHOTS },
  "/api/vixts": { GET: SCAN_SNAPSHOTS },
  "/api/vol-cone": { GET: SCAN_SNAPSHOTS },
  "/api/yield-curve": { GET: SCAN_SNAPSHOTS },

  // ── demo VM FastAPI ──────────────────────────────────────────────────────
  "/api/attribution": { GET: { mode: "backend", endpoints: ["/attribution"] } },
  "/api/backtest/[strategy]": { GET: { mode: "backend", endpoints: ["/backtest/"] } },
  "/api/futures/chain": { GET: { mode: "backend", endpoints: ["/futures/chain"] } },
  "/api/garch-convergence/scan": { POST: { mode: "backend", endpoints: ["/garch-convergence/scan"] } },
  "/api/gex/share": { POST: { mode: "backend", endpoints: ["/gex/share"] } },
  "/api/index-options/chain": { GET: { mode: "backend", endpoints: ["/index-options/chain"] } },
  "/api/informed-flow/[ticker]": { GET: { mode: "backend", endpoints: ["/informed-flow/"] } },
  "/api/internals/share": { POST: { mode: "backend", endpoints: ["/internals/share"] } },
  "/api/knowledge/prior-evals": { GET: { mode: "backend", endpoints: ["/knowledge/prior-evals"] } },
  "/api/knowledge/search": { POST: { mode: "backend", endpoints: ["/knowledge/search"] } },
  "/api/leap/scan": { POST: { mode: "backend", endpoints: ["/leap/scan"] } },
  "/api/llm-token-index": { GET: { mode: "backend", endpoints: ["/llm-token-index"] } },
  "/api/menthorq/cta/share": { POST: { mode: "backend", endpoints: ["/cta/share"] } },
  "/api/regime/share": { POST: { mode: "backend", endpoints: ["/regime/share"] } },
  "/api/scanner/bounce/scan": { POST: { mode: "backend", endpoints: ["/bounce-setup/scan"] } },
  "/api/scanner/strength/scan": { POST: { mode: "backend", endpoints: ["/strength-confirmation/scan"] } },
  "/api/scanner/vol-skew-mr/scan": { POST: { mode: "backend", endpoints: ["/vol-skew-mr/scan"] } },
  "/api/short-availability/[ticker]": { GET: { mode: "backend", endpoints: ["/short-availability/"] } },
  "/api/streaks": { GET: { mode: "backend", endpoints: ["/streaks/"] } },
  "/api/ticker/ratings": { GET: { mode: "backend", endpoints: ["/ticker/ratings"] } },
  "/api/vcg/share": { POST: { mode: "backend", endpoints: ["/vcg/share"] } },

  // ── third-party APIs called from Next.js ─────────────────────────────────
  "/api/futures-quote": { GET: { mode: "external", sources: [YAHOO] } },
  "/api/headlines": {
    GET: { mode: "external", sources: ["api.mktnews.net"], note: "Exists only on the demo deployment." },
  },
  "/api/index-quote": { GET: { mode: "external", sources: [YAHOO] } },
  "/api/previous-close": {
    POST: { mode: "external", sources: ["unusualwhales.com", YAHOO], note: "IB realtime first; unreachable on demo." },
  },
  "/api/risk-free-rate": { GET: { mode: "external", sources: ["fredgraph.csv"] } },
  "/api/ticker/news": { GET: { mode: "external", sources: ["unusualwhales.com", YAHOO] } },
  "/api/yield-curve/live": { GET: { mode: "external", sources: [YAHOO] } },

  // ── signed-out surfaces ──────────────────────────────────────────────────
  "/api/gex/share/content": {
    GET: { mode: "public", endpoints: ["/share/content"], note: "Share-card preview proxied from the backend." },
  },
  "/api/internals/share/content": {
    GET: { mode: "public", endpoints: ["/share/content"], note: "Share-card preview proxied from the backend." },
  },
  "/api/menthorq/cta/share/content": {
    GET: { mode: "public", note: "Reads reports/ from local disk; 404 on Vercel." },
  },
  "/api/regime/share/content": {
    GET: { mode: "public", note: "Reads reports/ from local disk; 404 on Vercel." },
  },
  "/api/vcg/share/content": {
    GET: { mode: "public", note: "Reads reports/ from local disk; 404 on Vercel." },
  },
  "/api/share/pnl": { GET: { mode: "public", note: "Renders a card from query params only." } },
  "/api/probe/freshness": {
    GET: {
      mode: "public",
      tables: ["service_health", "vcg_snapshots", "gex_snapshots", "journal"],
      note: "Bearer-token probe; no Clerk session.",
    },
  },
  "/api/webhooks/clerk": {
    POST: {
      mode: "public",
      tables: ["demo_users"],
      writes: ["demo_users", "demo_webhook_events"],
      note: "Svix-verified user.created provisioning.",
    },
  },
  "/api/webhooks/tradingview/[token]": {
    POST: { mode: "public", writes: ["tv_alert_events"], note: "Path-token webhook." },
  },

  // ── infrastructure ───────────────────────────────────────────────────────
  "/api/flex-token": { GET: { mode: "infra", note: "Disk token metadata; absent on Vercel." } },
  "/api/models": {
    GET: { mode: "infra", tables: ["llm_model_catalog"], note: "Model picker catalog; built-in fallback." },
  },
  "/api/prices": {
    GET: { mode: "infra", note: "Deprecated: static 405." },
    POST: { mode: "infra", note: "Deprecated: static 405." },
  },
  "/api/version": { GET: { mode: "infra", note: "Release metadata from env." } },

  // ── known gaps ───────────────────────────────────────────────────────────
  "/api/menthorq/[command]/image": {
    GET: { mode: "unavailable", note: "Reads data/menthorq_cache from disk only; 404 on Vercel." },
  },
  "/api/menthorq/cta/image": {
    GET: { mode: "unavailable", note: "Reads data/menthorq_cache from disk only, not menthorq_cta; 404 on Vercel." },
  },
};
