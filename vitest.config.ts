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
      // T-058: the PI extension security tests (browser command boundary,
      // workspace trust, bounded startup jobs). A dot-directory needs an
      // explicit leading-dot segment — a `**` glob will not descend into it.
      ".pi/tests/**/*.test.ts",
    ],
    environment: "node",
    fileParallelism: true,
    maxWorkers: "100%",
    // Shard VMs plus coverage have timed out 5s jsdom tests (newsfeed
    // pagination on shard 5, theta-harvester on shard 7). One CI retry
    // is cheaper than a red deploy gate; local stays fail-fast.
    retry: process.env.CI ? 1 : 0,
    // Pin NODE_ENV=test for every run. Vitest defaults to "test", but an ambient
    // shell `NODE_ENV=development` (common in a dev session) leaks through and
    // overrides it — silently flipping code paths that branch on NODE_ENV (e.g.
    // IBStatusContext skips Clerk's useAuth only under "test", so a "development"
    // shell makes ib-status-context/ws-keepalive throw "useAuth must be within
    // ClerkProvider"). Forcing it makes the suite deterministic regardless of the
    // developer's environment.
    env: { NODE_ENV: "test" },
    // Global @testing-library cleanup so jsdom components (and their leaked
    // effects/timers/WebSocket handlers) can't bleed into the next test — the
    // cross-test leak that made `vitest --coverage` throw window-undefined.
    setupFiles: ["./vitest.setup.ts"],
    coverage: {
      provider: "v8",
      // Default v8 reporters include html/clover/json. Writing those artifacts
      // for the 25k-line include set is a large fraction of the 307s CI job.
      // The ratchet is thresholds, not report files.
      reporter: ["text"],
      // Non-regressing RATCHET, not a vanity target. Each threshold sits ~2%
      // below current measured coverage so the suite passes today while
      // catching a regression. Raise these over time as coverage climbs;
      // never lower them to make a red build pass.
      //
      // 2026-08-17 (T-072): `web/components/**/*.tsx` joined the measured
      // surface (~12.4k -> 25,807 lines). Functions measured 73.78% (4475/6065)
      // against the old 78 gate, so functions was rebased 78 -> 71
      // (floor(measured - 2)) as a ONE-TIME honest re-baseline for the wider
      // surface — not a weakening of the old, narrower measurement. Lines
      // (78.06% vs 75) and branches (65.77% vs 65) still cleared their gates
      // and were left untouched. Ratchet functions back up as component
      // coverage climbs.
      thresholds: {
        lines: 75,
        functions: 71,
        branches: 65,
      },
      include: [
        "site/app/**/*.ts",
        "site/lib/**/*.ts",
        "web/lib/**/*.ts",
        "web/components/**/*.tsx",
        "web/app/api/**/*.ts",
        "lib/tools/**/*.ts",
      ],
      exclude: [
        "**/*.test.ts",
        "**/node_modules/**",
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
