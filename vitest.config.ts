import { defineConfig } from "vitest/config";
import { resolve } from "path";

export default defineConfig({
  // Repo root so `web/tests/**` includes match when `npm run test` runs from `web/`.
  root: resolve(__dirname),
  resolve: {
    alias: {
      "@tools": resolve(__dirname, "lib/tools"),
      "@/lib": resolve(__dirname, "web/lib"),
      "@": resolve(__dirname, "web"),
    },
  },
  test: {
    include: [
      "lib/tools/__tests__/**/*.test.ts",
      "site/lib/**/*.test.ts",
      "scripts/lib/**/*.test.js",
      "web/tests/**/*.test.ts",
      "web/tests/**/*.test.tsx",
    ],
    environment: "node",
    coverage: {
      provider: "v8",
      // Non-regressing RATCHET, not a vanity target. Each threshold sits 1-4%
      // below current measured coverage (~79% lines) so the suite passes today
      // while catching a regression. Raise these over time as coverage climbs;
      // never lower them to make a red build pass.
      thresholds: {
        lines: 75,
        functions: 78,
        branches: 65,
      },
      include: [
        "site/app/**/*.ts",
        "site/lib/**/*.ts",
        "web/lib/**/*.ts",
        "web/app/api/**/*.ts",
        "lib/tools/**/*.ts",
      ],
      exclude: [
        "**/*.test.ts",
        "**/node_modules/**",
        "web/lib/use*.ts",        // React hooks need jsdom
        "web/lib/OrderActionsContext.tsx",
        "web/lib/TickerDetailContext.tsx",
        "web/lib/types.ts",       // Pure type definitions, no runtime code
        "lib/tools/pi-tools.ts",  // PI framework registration, untestable without PI
        "lib/tools/schemas/index.ts",   // Re-export barrel
        "lib/tools/wrappers/index.ts",  // Re-export barrel
        "lib/tools/wrappers/fetch-ticker.ts", // Thin runScript wrapper
        "lib/tools/wrappers/ib-order-manage.ts", // Thin runScript wrapper
        "lib/tools/wrappers/ib-orders.ts",  // Thin runScript wrapper
        "lib/tools/wrappers/ib-sync.ts",    // Thin runScript wrapper
        "lib/tools/wrappers/scanner.ts",    // Thin runScript wrapper
        "web/app/api/pi/**",         // Large PI dispatcher, tested via integration.test.ts
        "web/app/api/prices/**",     // WebSocket client, needs live IB server
        "web/app/api/blotter/**",    // Spawns Python subprocess for Flex Query
        "web/app/api/discover/**",   // Spawns Python subprocess for discover.py
        "web/app/api/flow-analysis/**", // Spawns Python subprocess for flow_analysis.py
        "web/app/api/scanner/**",    // Spawns Python subprocess for scanner.py
      ],
    },
  },
});
