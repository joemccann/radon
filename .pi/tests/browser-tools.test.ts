/**
 * Browser tool command-boundary regressions.
 *
 * Collected by the root vitest gate (`vitest.config.ts` include list). It used
 * to run only when someone typed `bun .pi/tests/browser-tools.test.ts` by hand,
 * which meant the command-injection and executable-bit boundaries below were
 * revertable line by line with CI still green (T-058).
 */

import { test } from "vitest";
import * as assert from "node:assert";
import { chmodSync, mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildBrowserInvocation,
  isExecutableFile,
  parseBrowserCommand,
} from "../extensions/browser-tools.ts";

test("parses quoted browser arguments without shell evaluation", () => {
  assert.deepStrictEqual(
    parseBrowserCommand('fill @e3 "hello world; $(touch /tmp/pwned)"'),
    ["fill", "@e3", "hello world; $(touch /tmp/pwned)"],
  );
});

test("rejects unknown browser verbs", () => {
  assert.throws(() => parseBrowserCommand("eval document.cookie"), /unsupported browser command/);
  assert.throws(() => parseBrowserCommand("connect 9222"), /unsupported browser command/);
});

test("rejects options outside the browser command allowlist", () => {
  assert.throws(
    () => parseBrowserCommand("open https://example.com --allow-file-access"),
    /unsupported browser option/,
  );
  assert.throws(
    () => parseBrowserCommand("snapshot --profile /tmp/browser-profile"),
    /unsupported browser option/,
  );
});

test("builds one executable plus discrete argv", () => {
  assert.deepStrictEqual(
    buildBrowserInvocation(
      { executable: "npx", prefixArgs: ["--no-install", "agent-browser"] },
      {
        command: 'fill @e3 "hello world"',
        session: "review-1",
        headed: true,
        json: true,
      },
    ),
    {
      executable: "npx",
      argv: [
        "--no-install",
        "agent-browser",
        "--session",
        "review-1",
        "--headed",
        "--json",
        "fill",
        "@e3",
        "hello world",
      ],
    },
  );
});

test("rejects unsafe session names", () => {
  assert.throws(
    () => buildBrowserInvocation(
      { executable: "agent-browser", prefixArgs: [] },
      { command: "snapshot -i", session: 'safe"; touch /tmp/pwned' },
    ),
    /invalid browser session/,
  );
});

test("falls back when NVM has no agent-browser executable", () => {
  const root = mkdtempSync(join(tmpdir(), "browser-tool-"));
  const directory = join(root, "agent-browser");
  mkdirSync(directory);
  assert.strictEqual(isExecutableFile(directory), false);
  const file = join(root, "agent-browser-file");
  writeFileSync(file, "#!/bin/sh\n");
  chmodSync(file, 0o644);
  assert.strictEqual(isExecutableFile(file), false);
  chmodSync(file, 0o755);
  assert.strictEqual(isExecutableFile(file), true);
});
