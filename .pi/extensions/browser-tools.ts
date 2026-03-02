import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { execSync } from "child_process";

const BROWSER_PATH = "/Users/joemccann/.nvm/versions/node/v22.12.0/bin/agent-browser";

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
    async execute({ command, session, headed, json }) {
      const args: string[] = [];
      if (session) args.push(`--session "${session}"`);
      if (headed) args.push("--headed");
      if (json) args.push("--json");
      
      const fullCommand = `${BROWSER_PATH} ${args.join(" ")} ${command}`;
      
      try {
        const output = execSync(fullCommand, { 
          encoding: "utf-8",
          timeout: 60000,
          maxBuffer: 10 * 1024 * 1024,
        });
        return output;
      } catch (err: any) {
        return `Error: ${err.message}\n${err.stdout || ""}\n${err.stderr || ""}`;
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
