import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { execFileSync } from "node:child_process";
import { accessSync, constants, existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

export interface BrowserExecutable {
  executable: string;
  prefixArgs: string[];
}

export function isExecutableFile(candidate: string): boolean {
  try {
    if (!statSync(candidate).isFile()) return false;
    accessSync(candidate, process.platform === "win32" ? constants.F_OK : constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

interface BrowserToolParams {
  command: string;
  session?: string;
  headed?: boolean;
  json?: boolean;
}

const ALLOWED_BROWSER_COMMANDS = new Set([
  "open", "click", "dblclick", "type", "fill", "press", "keyboard",
  "hover", "focus", "check", "uncheck", "select", "drag", "scroll",
  "scrollintoview", "wait", "screenshot", "snapshot", "get", "is", "find",
  "mouse", "back", "forward", "reload", "close", "tab", "diff", "console",
  "errors", "highlight",
]);

const ALLOWED_COMMAND_OPTIONS: Record<string, Set<string>> = {
  snapshot: new Set(["-i", "--interactive", "-c", "--compact", "-d", "--depth", "-s", "--selector"]),
  screenshot: new Set(["-f", "--full", "--annotate"]),
  wait: new Set(["--load"]),
  find: new Set(["--name", "--exact"]),
  console: new Set(["--clear"]),
  errors: new Set(["--clear"]),
};

/** Split a command string into argv without invoking a shell. */
function tokenize(command: string): string[] {
  if (typeof command !== "string" || command.includes("\0")) {
    throw new Error("invalid browser command");
  }

  const argv: string[] = [];
  let current = "";
  let quote: "'" | '"' | null = null;
  let escaped = false;

  for (const char of command.trim()) {
    if (escaped) {
      current += char;
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (char === quote) quote = null;
      else current += char;
      continue;
    }
    if (char === "'" || char === '"') {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) {
        argv.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }

  if (escaped || quote) throw new Error("unterminated browser command quoting");
  if (current) argv.push(current);
  return argv;
}

export function parseBrowserCommand(command: string): string[] {
  const argv = tokenize(command);
  const verb = argv[0];
  if (!verb || !ALLOWED_BROWSER_COMMANDS.has(verb)) {
    throw new Error(`unsupported browser command: ${verb || "<empty>"}`);
  }

  const allowedOptions = ALLOWED_COMMAND_OPTIONS[verb] ?? new Set<string>();
  for (const arg of argv.slice(1)) {
    if (arg.startsWith("-") && !allowedOptions.has(arg)) {
      throw new Error(`unsupported browser option for ${verb}: ${arg}`);
    }
  }
  return argv;
}

export function buildBrowserInvocation(
  browser: BrowserExecutable,
  params: BrowserToolParams,
): { executable: string; argv: string[] } {
  const argv = [...browser.prefixArgs];
  if (params.session) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(params.session)) {
      throw new Error("invalid browser session");
    }
    argv.push("--session", params.session);
  }
  if (params.headed) argv.push("--headed");
  if (params.json) argv.push("--json");
  argv.push(...parseBrowserCommand(params.command));
  return { executable: browser.executable, argv };
}

/**
 * Locate agent-browser executable.
 * Priority:
 * 1. `which agent-browser` (in PATH)
 * 2. Common NVM locations
 * 3. Global npm bin
 * 4. Fallback to `npx agent-browser`
 */
function findAgentBrowser(): BrowserExecutable {
  // Try PATH first
  try {
    const locator = process.platform === "win32" ? "where" : "which";
    const whichResult = execFileSync(locator, ["agent-browser"], { encoding: "utf-8" })
      .split(/\r?\n/)[0]
      .trim();
    if (whichResult && isExecutableFile(whichResult)) {
      return { executable: whichResult, prefixArgs: [] };
    }
  } catch {
    // Not in PATH, continue searching
  }

  const home = process.env.HOME || process.env.USERPROFILE || "";
  
  // Common locations to check
  const candidates = [
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
      const versions = readdirSync(nvmPath).sort().reverse(); // Latest first
      for (const version of versions) {
        const binPath = join(nvmPath, version, "bin/agent-browser");
        if (isExecutableFile(binPath)) {
          return { executable: binPath, prefixArgs: [] };
        }
      }
    }
  } catch {
    // Continue to other candidates
  }

  // Check other candidate paths
  for (const candidate of candidates) {
    if (isExecutableFile(candidate)) {
      return { executable: candidate, prefixArgs: [] };
    }
  }

  // Fallback: use npx (will download if needed)
  return { executable: "npx", prefixArgs: ["--no-install", "agent-browser"] };
}

// Cache the path after first lookup
let cachedBrowserPath: BrowserExecutable | null = null;

function getBrowserPath(): BrowserExecutable {
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
        const invocation = buildBrowserInvocation(getBrowserPath(), params ?? {});
        const output = execFileSync(invocation.executable, invocation.argv, {
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
    handler: async (_args, _ctx) => {
      pi.sendUserMessage("Take a browser snapshot with interactive elements only");
    },
  });
}
