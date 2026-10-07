// T-537: observe every fake launch with a sealed, entirely synthetic environment.
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const browserModule = new URL("../../scripts/newsfeed/browser.js", import.meta.url).href;

describe.each(["complete", "sparse"])("Chromium environment (%s)", (density) => {
  it.each(["normal", "retry", "disabled"])("isolates the %s launch", (mode) => {
    const directory = mkdtempSync(join(tmpdir(), "radon-browser-env-"));
    const runtime: Record<string, string> = { PATH: "/synthetic/bin", HOME: "/home/synthetic" };
    if (density === "complete") {
      Object.assign(runtime, {
        USER: "synthetic", LOGNAME: "synthetic", LANG: "en_US.UTF-8", LANGUAGE: "en",
        LC_ALL: "en_US.UTF-8", LC_CTYPE: "en_US.UTF-8", TZ: "America/New_York", TMPDIR: directory,
        DISPLAY: ":17", WAYLAND_DISPLAY: "wayland-synthetic", XDG_RUNTIME_DIR: "/run/synthetic",
        XDG_CONFIG_HOME: "/home/synthetic/config", XDG_CACHE_HOME: "/home/synthetic/cache",
        FONTCONFIG_PATH: "/synthetic/fonts", FONTCONFIG_FILE: "/synthetic/fonts.conf",
      });
    }
    const childEnv = {
      ...runtime,
      TURSO_AUTH_TOKEN: "synthetic-database", TURSO_DB_URL: "https://synthetic.invalid",
      UW_TOKEN: "synthetic-provider", ANTHROPIC_API_KEY: "synthetic-provider",
      FUTURE_SERVICE_CREDENTIAL: "synthetic-future", NODE_ENV: "test",
      ...(mode === "disabled" ? { PLAYWRIGHT_CHROMIUM_SANDBOX: "0" } : {}),
    };
    const code = `
      const { createBrowser } = await import(${JSON.stringify(browserModule)});
      const mode = ${JSON.stringify(mode)};
      const calls = [];
      let closed = 0;
      console.error = () => {};
      const context = { newPage: async () => ({}), close: async () => { closed++; } };
      const browser = { newContext: async () => context, close: async () => { closed++; } };
      const launcher = { launch: async (options) => {
        calls.push({ env: options.env, args: options.args ?? [] });
        if (mode === "retry" && calls.length === 1) throw new Error("No usable sandbox!");
        return browser;
      } };
      const handle = await createBrowser({
        storageStatePath: ${JSON.stringify(join(directory, "storage.json"))}, launcher,
      });
      await handle.close();
      console.log(JSON.stringify({ calls, closed }));
    `;
    try {
      // Never inherit the workstation environment or launch an actual browser.
      const receipt = JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", code], {
        env: childEnv, encoding: "utf8", timeout: 10_000,
      })) as { calls: { env: Record<string, string>; args: string[] }[]; closed: number };
      expect(receipt.calls).toHaveLength(mode === "retry" ? 2 : 1);
      for (const call of receipt.calls) expect(call.env).toEqual(runtime);
      expect(receipt.calls.map((call) => call.args)).toEqual(
        mode === "normal" ? [[]] : mode === "retry"
          ? [[], ["--no-sandbox", "--disable-dev-shm-usage"]]
          : [["--no-sandbox", "--disable-dev-shm-usage"]],
      );
      expect(receipt.closed).toBe(2);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  }, 15_000);
});
