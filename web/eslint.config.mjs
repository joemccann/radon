import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextVitals,
  {
    rules: {
      // React Compiler–oriented rules from eslint-config-next: enable incrementally; do not block `npm run lint`.
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/purity": "off",
      "react-hooks/refs": "off",
      "react-hooks/immutability": "off",
      "react-hooks/preserve-manual-memoization": "off",

      // Order-risk chokepoint enforcement. The math primitives
      // (`computeOrderRisk`, `augmentOrderLegsWithPortfolioCoverage`) and
      // the brand symbol must NOT be imported from production code — every
      // order surface flows through `useOrderRisk` / `<OrderRiskGate>` in
      // `@/lib/order/risk`. Test files are exempt via the global
      // `tests/**` + `e2e/**` ignore patterns above.
      //
      // Why: three production bugs in eight days (AAOI 2026-05-19, WULF
      // 2026-05-26 morning, RR 2026-05-26 afternoon) all shipped because
      // a surface hand-built risk math and missed portfolio coverage.
      // The chokepoint pattern (brand + lint + dev-mode runtime assert)
      // makes that impossible. See `tasks/order-risk-chokepoint-refactor.md`.
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "@/lib/order/risk/internal/*",
                "**/lib/order/risk/internal/*",
                "@/lib/order/risk/__test_only__",
                "**/lib/order/risk/__test_only__",
                "@/lib/orderRisk",
                "**/lib/orderRisk",
              ],
              message:
                "Use `useOrderRisk` / `<OrderRiskGate>` from `@/lib/order/risk` — the raw math is module-private so every surface goes through the portfolio-aware augmentation pipeline. See web/CLAUDE.md → Order-risk chokepoint.",
            },
          ],
        },
      ],
    },
  },
  {
    // Edge-runtime perimeter enforcement. `middleware.ts` runs in the Edge
    // runtime where `node:*` modules don't exist — tsc, vitest (Node), and
    // `next build` all pass with a node:* import in the middleware graph;
    // the crash only manifests at REQUEST time in production ("Native
    // module not found: node:crypto", 2026-05-15, 36 minutes of 500s on
    // every route). This block covers the middleware and every local module
    // it imports. If middleware.ts grows a new local import, add that file
    // here — the CI perimeter-smoke job (`next start` + curl) is the
    // request-time backstop. See feedback_middleware_edge_runtime.md.
    //
    // Uses no-restricted-syntax (not no-restricted-imports) so it layers on
    // top of the global no-restricted-imports rule instead of replacing it
    // for these files.
    files: ["middleware.ts", "lib/apiContracts.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "ImportDeclaration[source.value=/^node:/]",
          message:
            "middleware.ts and its import graph run in the Edge runtime — node:* modules crash at request time in production. Use Web APIs (globalThis.crypto, fetch, URL). See feedback_middleware_edge_runtime.md.",
        },
        {
          selector:
            "ImportDeclaration[source.value=/^(assert|async_hooks|buffer|child_process|cluster|crypto|dgram|dns|fs|http|http2|https|net|os|path|perf_hooks|process|querystring|readline|stream|string_decoder|tls|util|v8|vm|worker_threads|zlib)$/]",
          message:
            "Bare Node builtin import in the Edge-runtime middleware graph — crashes at request time in production. Use Web APIs instead. See feedback_middleware_edge_runtime.md.",
        },
      ],
    },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "playwright-report/**",
    "test-results/**",
    "tests/**",
    "e2e/**",
  ]),
]);
