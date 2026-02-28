import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";

export default function (pi: ExtensionAPI) {
  // Load persona and docs content at startup
  let personaContent = "";
  let docsContent = "";

  const loadProjectMemory = (cwd: string) => {
    const files = [
      { path: "PERSONA.md", label: "Persona" },
      { path: "docs/prompt.md", label: "Spec" },
      { path: "docs/plans.md", label: "Plans" },
      { path: "docs/implement.md", label: "Runbook" },
      { path: "docs/status.md", label: "Status" },
    ];

    const loaded: string[] = [];
    const contents: string[] = [];

    for (const file of files) {
      const fullPath = path.join(cwd, file.path);
      if (fs.existsSync(fullPath)) {
        const content = fs.readFileSync(fullPath, "utf-8");
        contents.push(`\n\n--- ${file.label.toUpperCase()} (${file.path}) ---\n${content}`);
        loaded.push(file.label);
      }
    }

    return { loaded, content: contents.join("\n") };
  };

  // Inject persona and docs into system prompt on first agent start
  pi.on("before_agent_start", async (event, ctx) => {
    // Load project memory
    const { loaded, content } = loadProjectMemory(ctx.cwd);
    
    if (content && loaded.length > 0) {
      // Inject as additional system prompt context
      const injectedPrompt = `
## PROJECT MEMORY (Auto-loaded at session start)

The following durable project memory has been loaded. Follow these instructions precisely.

${content}

---
END PROJECT MEMORY
---
`;
      
      return {
        systemPrompt: event.systemPrompt + "\n" + injectedPrompt,
      };
    }
  });

  // Notify on session start
  pi.on("session_start", async (_event, ctx) => {
    const { loaded } = loadProjectMemory(ctx.cwd);
    
    if (loaded.length > 0) {
      ctx.ui.notify(`Loaded: ${loaded.join(", ")}`, "info");
    }
  });
}
