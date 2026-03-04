import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { execSync } from "child_process";
import { existsSync } from "fs";
import { join } from "path";

/**
 * Locate agent-browser executable.
 * Priority:
 * 1. `which agent-browser` (in PATH)
 * 2. Common NVM locations
 * 3. Global npm bin
 * 4. Fallback to `npx agent-browser`
 */
function findAgentBrowser(): string {
  // Try PATH first
  try {
    const whichResult = execSync("which agent-browser", { encoding: "utf-8" }).trim();
    if (whichResult && existsSync(whichResult)) {
      return whichResult;
    }
  } catch {
    // Not in PATH, continue searching
  }

  const home = process.env.HOME || process.env.USERPROFILE || "";
  
  // Common locations to check
  const candidates = [
    // NVM paths (try to find any installed node version)
    join(home, ".nvm/versions/node"),
    // Global npm bin
    join(home, ".npm-global/bin/agent-browser"),
    join(home, ".npm/bin/agent-browser"),
    // Homebrew on macOS
    "/usr/local/bin/agent-browser",
    "/opt/homebrew/bin/agent-browser",
  ];

  // Check NVM directory for any node version
  const nvmPath = join(home, ".nvm/versions/node");
  try {
    if (existsSync(nvmPath)) {
      const { readdirSync } = require("fs");
      const versions = readdirSync(nvmPath).sort().reverse(); // Latest first
      for (const version of versions) {
        const binPath = join(nvmPath, version, "bin/agent-browser");
        if (existsSync(binPath)) {
          return binPath;
        }
      }
    }
  } catch {
    // Continue to other candidates
  }

  // Check other candidate paths
  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }

  // Fallback: use npx (will download if needed)
  return "npx agent-browser";
}

// Cache the path after first lookup
let cachedBrowserPath: string | null = null;

function getBrowserPath(): string {
  if (!cachedBrowserPath) {
    cachedBrowserPath = findAgentBrowser();
  }
  return cachedBrowserPath;
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "browser",
    label: "Browser Automation",
    description: "Control a browser for web automation. Commands: open, click, type, fill, snapshot, screenshot, get, etc.",
    parameters: Type.Object({
      command: Type.String({ description: "Command to run (e.g., 'open https://example.com', 'snapshot -i', 'click @e2')" }),
      session: Type.Optional(Type.String({ description: "Session name for isolation" })),
      headed: Type.Optional(Type.Boolean({ description: "Show browser window (not headless)" })),
      json: Type.Optional(Type.Boolean({ description: "Return JSON output" })),
    }),
    async execute(_toolCallId: string, params: any) {
      try {
        const { command, session, headed, json } = params ?? {};
        const browserPath = getBrowserPath();
        const args: string[] = [];
        if (session) args.push(`--session "${session}"`);
        if (headed) args.push("--headed");
        if (json) args.push("--json");

        const fullCommand = `${browserPath} ${args.join(" ")} ${command}`;

        const output = execSync(fullCommand, {
          encoding: "utf-8",
          timeout: 60000,
          maxBuffer: 10 * 1024 * 1024,
          env: { ...process.env, PATH: process.env.PATH },
        });
        return {
          content: [{ type: "text" as const, text: output }],
        };
      } catch (err: any) {
        return {
          content: [{ type: "text" as const, text: `Error: ${err.message}\n${err.stdout || ""}\n${err.stderr || ""}` }],
        };
      }
    },
  });

  // Quick snapshot command
  pi.registerCommand("snap", {
    description: "Get browser accessibility snapshot (interactive elements)",
    handler: async (_args, ctx) => {
      ctx.sendUserMessage("Take a browser snapshot with interactive elements only");
    },
  });
}
