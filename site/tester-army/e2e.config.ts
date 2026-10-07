import type { E2EConfig } from "e2e";
import { web } from "@e2e-dev/web";

export default {
  projectId: "radon-public-site",
  tests: "tests/**/*.e2e.ts",
  workers: 1,
  retries: 0,
  cache: "off",
  trace: "retain-on-failure",
  timeout: 120_000,
  assertionTimeout: 10_000,
  reporters: ["list", "junit", "markdown"],
  targets: [{
    name: "chromium",
    engine: web({ viewport: { width: 1440, height: 900 } }),
    app: {
      url: "http://127.0.0.1:3342",
      environment: "test",
      command: {
        executable: "node",
        args: ["node_modules/next/dist/bin/next", process.env.TESTER_ARMY_SERVER_MODE === "start" ? "start" : "dev", "--hostname", "127.0.0.1", "--port", "3342", ...(process.env.TESTER_ARMY_SERVER_MODE === "start" ? [] : ["--webpack"])],
        cwd: "..",
        env: { NEXT_DIST_DIR: ".next-tester-army" },
        startupTimeout: 120_000,
        log: ".e2e/server.log",
        reuseExisting: false,
      },
    },
  }],
} satisfies E2EConfig;
