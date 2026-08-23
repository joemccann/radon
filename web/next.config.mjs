import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Baseline security headers for all routes. HSTS only when explicitly safe
 * (see below).
 *
 * The Content-Security-Policy is NO LONGER emitted here. It is now an ENFORCED,
 * per-request nonce'd policy owned by web/middleware.ts (a static next.config
 * header can't carry a per-request nonce). See buildCspWithNonce() there.
 */
function securityHeaders() {
  const headers = [
    { key: "X-Frame-Options", value: "DENY" },
    // Authenticated terminal — never indexed. SEO lives on radon.run (site/).
    // Googlebot is allowed to crawl (see app/robots.ts) so this header can
    // drop URLs already in the index; robots.txt Disallow would hide it.
    { key: "X-Robots-Tag", value: "noindex, nofollow" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    {
      key: "Permissions-Policy",
      value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    },
  ];
  // Avoid HSTS on local `next start` (can pin broken HTTPS on localhost). Vercel sets VERCEL=1.
  if (process.env.VERCEL === "1" || process.env.RADON_ENABLE_HSTS === "1") {
    headers.push({
      key: "Strict-Transport-Security",
      value: "max-age=31536000; includeSubDomains; preload",
    });
  }
  return headers;
}

// Hetzner mounts the live data tree beside the app checkout. Routes that
// fall back to disk must not package that mutable tree into the serverless
// function trace — production data alone is >128 MiB and fails deploy audit.
const HOST_DATA_TRACE_EXCLUDES = ["../data/**/*"];
const HOST_DATA_TRACE_ROUTES = [
  "/api/breadth",
  "/api/catalysts",
  "/api/discover",
  "/api/flow-analysis",
  "/api/flow-analysis/[ticker]",
  "/api/flow-surprise",
  "/api/futures/chain",
  "/api/gamma-rotation",
  "/api/garch-convergence",
  "/api/gex",
  "/api/internals",
  "/api/leap",
  "/api/margin-debt",
  "/api/menthorq/cta",
  "/api/performance",
  "/api/regime",
  "/api/scanner",
  "/api/ticker/seasonality",
  "/api/vcg",
];

const config = {
  outputFileTracingRoot: resolve(__dirname, ".."),
  outputFileTracingExcludes: Object.fromEntries(
    HOST_DATA_TRACE_ROUTES.map((route) => [route, HOST_DATA_TRACE_EXCLUDES]),
  ),
  // Tree-shake lucide-react named imports so every shell route pays only for
  // icons it actually uses (React BP #2 / skill-stack T10).
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
  turbopack: {},
  webpack: (config) => {
    config.resolve.alias["@tools"] = resolve(__dirname, "..", "lib", "tools");
    return config;
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "media.radon.run" },
    ],
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders(),
      },
    ];
  },
};

export default config;
