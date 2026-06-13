import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Content-Security-Policy shipped as Report-Only so it can't white-screen
 * the app while we audit violations. Tighten + flip to enforcing once the
 * violation report stream is clean. See tasks/csp-enforcement.md for the
 * tracking plan.
 *
 * Permissive-but-real policy rationale:
 *  - 'unsafe-inline' / 'unsafe-eval' required by Next.js runtime + Clerk SDK
 *  - clerk.radon.run + *.clerk.accounts.dev for Clerk auth flows
 *  - media.radon.run for image CDN (rsync-fed Caddy on Hetzner)
 *  - fonts.googleapis.com / fonts.gstatic.com for web fonts
 *  - img-src 'self' https: data: to cover all image hosts without breakage
 *  - connect-src 'self' wss: https: covers WebSocket relay + Turso + UW API
 */
function buildCsp() {
  const clerkHosts =
    "https://clerk.radon.run https://*.clerk.accounts.dev https://clerk.accounts.dev";
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline' 'unsafe-eval' ${clerkHosts}`,
    `style-src 'self' 'unsafe-inline' https://fonts.googleapis.com`,
    `font-src 'self' https://fonts.gstatic.com data:`,
    `img-src 'self' https: data: blob:`,
    `connect-src 'self' wss: https:`,
    `frame-src 'self' ${clerkHosts}`,
    `frame-ancestors 'none'`,
    `object-src 'none'`,
    `base-uri 'self'`,
    `form-action 'self' ${clerkHosts}`,
  ];
  return directives.join("; ");
}

/** Baseline security headers for all routes. HSTS only when explicitly safe (see below). */
function securityHeaders() {
  const headers = [
    { key: "X-Frame-Options", value: "DENY" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    {
      key: "Permissions-Policy",
      value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    },
    // Report-Only: collects violations without blocking — safe to ship now.
    // Flip to Content-Security-Policy once violation stream is clean.
    { key: "Content-Security-Policy-Report-Only", value: buildCsp() },
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

const config = {
  outputFileTracingRoot: resolve(__dirname, ".."),
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
