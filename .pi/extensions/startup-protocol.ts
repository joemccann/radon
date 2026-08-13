import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";
import { execFileSync, spawn } from "node:child_process";

const STARTUP_JOB_TIMEOUT_MS = 120_000;
const STARTUP_JOB_OUTPUT_BYTES = 1_048_576;

type StartupJobResult = {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
};

/** Run detached startup work with one completion, bounded memory and deadline. */
export function runBoundedStartupJob(
  command: string,
  args: string[],
  cwd: string,
  onComplete: (result: StartupJobResult) => void,
  timeoutMs = STARTUP_JOB_TIMEOUT_MS,
) {
  const proc = spawn(command, args, {
    cwd,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  let completed = false;
  const append = (current: string, chunk: unknown) =>
    (current + String(chunk)).slice(-STARTUP_JOB_OUTPUT_BYTES);
  const finish = (code: number | null, timedOut: boolean) => {
    if (completed) return;
    completed = true;
    clearTimeout(timer);
    onComplete({ code, stdout, stderr, timedOut });
  };
  const timer = setTimeout(() => {
    stderr = append(stderr, "\nstartup job timed out");
    try {
      if (proc.pid && process.platform !== "win32") process.kill(-proc.pid, "SIGTERM");
      else proc.kill("SIGTERM");
    } catch {
      try { proc.kill("SIGKILL"); } catch { /* already exited */ }
    }
    finish(null, true);
  }, timeoutMs);
  timer.unref();
  proc.stdout?.on("data", (chunk) => { stdout = append(stdout, chunk); });
  proc.stderr?.on("data", (chunk) => { stderr = append(stderr, chunk); });
  proc.once("error", (error) => {
    stderr = append(stderr, `\n${error.message}`);
    finish(null, false);
  });
  proc.once("close", (code) => finish(code, false));
  proc.unref();
  return proc;
}

export interface WorkspaceTrust {
  root: string;
  revision: string;
}

function hashBytes(value: Buffer | string): string {
  return createHash("sha256").update(value).digest("hex");
}

export function createWorkspaceTrust(
  cwd: string,
  env: NodeJS.ProcessEnv = process.env,
): WorkspaceTrust | null {
  const configuredRoot = env.RADON_PI_TRUSTED_WORKSPACE;
  const configuredRevision = env.RADON_PI_TRUSTED_REVISION;
  if (!configuredRoot || !configuredRevision || !/^[0-9a-f]{40,64}$/i.test(configuredRevision)) {
    return null;
  }

  try {
    const root = fs.realpathSync(configuredRoot);
    if (fs.realpathSync(cwd) !== root) return null;
    const revision = execFileSync("git", ["rev-parse", "--verify", "HEAD^{commit}"], {
      cwd: root,
      encoding: "utf8",
      timeout: 5000,
    }).trim();
    if (revision !== configuredRevision) return null;
    return { root, revision };
  } catch {
    return null;
  }
}

export function resolveTrustedWorkspaceScript(
  trust: WorkspaceTrust,
  relativePath: string,
): string | null {
  if (path.isAbsolute(relativePath)) return null;
  const candidate = path.resolve(trust.root, relativePath);
  if (!candidate.startsWith(`${trust.root}${path.sep}`)) return null;

  try {
    const stat = fs.lstatSync(candidate);
    if (!stat.isFile() || stat.isSymbolicLink()) return null;
    if (fs.realpathSync(candidate) !== candidate) return null;
    const revisionBytes = execFileSync(
      "git",
      ["show", `${trust.revision}:${relativePath.replaceAll(path.sep, "/")}`],
      { cwd: trust.root, timeout: 5000, maxBuffer: 10 * 1024 * 1024 },
    );
    if (hashBytes(fs.readFileSync(candidate)) !== hashBytes(revisionBytes)) return null;
    return candidate;
  } catch {
    return null;
  }
}

/**
 * Startup Protocol Extension
 * 
 * Loads project documentation and core skills into context as durable memory.
 * Note: SYSTEM.md is loaded automatically by pi (defines agent identity).
 * Note: AGENTS.md is loaded automatically by pi (defines project workflow).
 * This extension adds docs/* and always-on skills for additional project context.
 * 
 * Also checks for pending X account scans based on last scan time.
 */

/**
 * Check if US options markets are currently open.
 * Options trade 9:30 AM - 4:00 PM Eastern Time, Monday-Friday.
 */
function nthWeekday(year: number, month: number, weekday: number, nth: number): number {
  const first = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  return 1 + ((weekday - first + 7) % 7) + (nth - 1) * 7;
}

function lastWeekday(year: number, month: number, weekday: number): number {
  const last = new Date(Date.UTC(year, month, 0));
  return last.getUTCDate() - ((last.getUTCDay() - weekday + 7) % 7);
}

function easterSunday(year: number): Date {
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(Date.UTC(year, month - 1, day));
}

function observedFixedHoliday(year: number, month: number, day: number): string {
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCDay() === 6) date.setUTCDate(day - 1);
  if (date.getUTCDay() === 0) date.setUTCDate(day + 1);
  return date.toISOString().slice(0, 10);
}

export function marketStateAt(now: Date): { isOpen: boolean; status: string } {
  
  // Convert to Eastern Time
  const etOptions: Intl.DateTimeFormatOptions = { 
    timeZone: 'America/New_York', 
    hour: 'numeric', 
    minute: 'numeric',
    hour12: false,
    weekday: 'short'
  };
  const etFormatter = new Intl.DateTimeFormat('en-US', etOptions);
  const parts = etFormatter.formatToParts(now);
  
  const weekday = parts.find(p => p.type === 'weekday')?.value || '';
  const month = Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "numeric" }).format(now));
  const day = Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", day: "numeric" }).format(now));
  const year = Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric" }).format(now));
  const hour = parseInt(parts.find(p => p.type === 'hour')?.value || '0', 10);
  const minute = parseInt(parts.find(p => p.type === 'minute')?.value || '0', 10);
  const timeInMinutes = hour * 60 + minute;
  
  // Market hours: 9:30 AM (570 mins) to 4:00 PM (960 mins)
  const marketOpen = 9 * 60 + 30;  // 570
  let marketClose = 16 * 60;      // 960
  
  // Check weekend
  if (weekday === 'Sat' || weekday === 'Sun') {
    return { isOpen: false, status: 'CLOSED (weekend)' };
  }

  const dateKey = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  const goodFriday = easterSunday(year);
  goodFriday.setUTCDate(goodFriday.getUTCDate() - 2);
  const holidays = new Set([
    observedFixedHoliday(year, 1, 1),
    `${year}-01-${String(nthWeekday(year, 1, 1, 3)).padStart(2, "0")}`,
    `${year}-02-${String(nthWeekday(year, 2, 1, 3)).padStart(2, "0")}`,
    goodFriday.toISOString().slice(0, 10),
    `${year}-05-${String(lastWeekday(year, 5, 1)).padStart(2, "0")}`,
    observedFixedHoliday(year, 6, 19),
    observedFixedHoliday(year, 7, 4),
    `${year}-09-${String(nthWeekday(year, 9, 1, 1)).padStart(2, "0")}`,
    `${year}-11-${String(nthWeekday(year, 11, 4, 4)).padStart(2, "0")}`,
    observedFixedHoliday(year, 12, 25),
  ]);
  if (holidays.has(dateKey)) return { isOpen: false, status: "CLOSED (holiday)" };
  const thanksgiving = nthWeekday(year, 11, 4, 4);
  if ((month === 11 && day === thanksgiving + 1) || (month === 12 && day === 24)) {
    marketClose = 13 * 60;
  }
  
  // Check time
  if (timeInMinutes < marketOpen) {
    const minsToOpen = marketOpen - timeInMinutes;
    const h = Math.floor(minsToOpen / 60);
    const m = minsToOpen % 60;
    return { isOpen: false, status: h > 0 ? `pre-market, ${h}h ${m}m to open` : `pre-market, ${m}m to open` };
  } else if (timeInMinutes >= marketClose) {
    return { isOpen: false, status: 'after hours' };
  } else {
    const minsToClose = marketClose - timeInMinutes;
    const h = Math.floor(minsToClose / 60);
    const m = minsToClose % 60;
    return { isOpen: true, status: h > 0 ? `OPEN (${h}h ${m}m to close)` : `OPEN (${m}m to close)` };
  }
}

function isMarketOpen(): { isOpen: boolean; status: string } {
  return marketStateAt(new Date());
}

// UI interface for notifications
interface NotifyUI {
  notify(message: string, level: "info" | "warning" | "error"): void;
}

/**
 * StartupTracker - Tracks and displays progress of all startup processes
 * 
 * Collects all process results and shows a single batched notification
 * at the end with all progress lines. This avoids TUI notification coalescence.
 */
export class StartupTracker {
  private processes: Map<string, { status: "pending" | "success" | "warning" | "error"; message?: string }> = new Map();
  private ui: NotifyUI;
  private total: number;
  private completionOrder: string[] = [];
  private messages: string[] = [];
  
  constructor(ui: NotifyUI, processNames: string[]) {
    this.ui = ui;
    this.total = processNames.length;
    processNames.forEach(name => this.processes.set(name, { status: "pending" }));
    // Immediately notify user of startup with check count
    this.ui.notify(`🚀 Startup: Running ${this.total} checks...`, "info");
  }
  
  /**
   * Mark a process as complete with its status and message
   */
  complete(name: string, status: "success" | "warning" | "error", message: string) {
    if (!this.processes.has(name)) {
      // Process wasn't registered, add it dynamically
      this.processes.set(name, { status: "pending" });
      this.total = this.processes.size;
    }
    
    this.processes.set(name, { status, message });
    this.completionOrder.push(name);
    
    const completed = this.completionOrder.length;
    const icon = status === "success" ? "✓" : status === "warning" ? "⚠️" : "❌";
    
    // Collect message instead of notifying immediately
    this.messages.push(`[${completed}/${this.total}] ${icon} ${message}`);
    
    // Check if all done
    if (completed === this.total) {
      this.showBatchedSummary();
    }
  }
  
  /**
   * Show all collected messages as a single batched notification
   */
  private showBatchedSummary() {
    const statuses = Array.from(this.processes.values());
    const successes = statuses.filter(s => s.status === "success").length;
    const warnings = statuses.filter(s => s.status === "warning").length;
    const errors = statuses.filter(s => s.status === "error").length;
    
    // Build final summary line
    let summaryLine: string;
    
    if (errors > 0) {
      summaryLine = `❌ Startup complete (${successes}/${this.total} passed, ${errors} failed)`;
    } else if (warnings > 0) {
      summaryLine = `⚠️ Startup complete (${successes}/${this.total} passed, ${warnings} warnings)`;
    } else {
      summaryLine = `✅ Startup complete (${this.total}/${this.total} passed)`;
    }
    
    // Combine all progress messages + summary (header was already shown at start)
    // Always use "info" level - status is conveyed by icons (✓, ⚠️, ❌) not color
    const fullOutput = [...this.messages, summaryLine].join("\n");
    
    this.ui.notify(fullOutput, "info");
  }
  
  /**
   * Get current status for a process
   */
  getStatus(name: string): "pending" | "success" | "warning" | "error" | undefined {
    return this.processes.get(name)?.status;
  }
  
  /**
   * Check if all processes are complete
   */
  isComplete(): boolean {
    return this.completionOrder.length === this.total;
  }
}

export function summarizeFreeTradeError(errorOutput: string, fallback = "Free trade analysis error"): string {
  const trimmed = errorOutput
    .split("\n")
    .map(line => line.trim())
    .filter(Boolean);

  const preferred = trimmed.find(line =>
    line.startsWith("Cannot verify current portfolio via IB:") ||
    line.startsWith("Cannot verify current portfolio via IB")
  );
  if (preferred) {
    return preferred;
  }

  const firstUseful = trimmed.find(line =>
    !line.startsWith("IB error ") &&
    !line.startsWith("Peer closed connection") &&
    !line.startsWith("API connection failed")
  );

  return firstUseful || trimmed[0] || fallback;
}

export default function (pi: ExtensionAPI) {
  const loadProjectDocs = (cwd: string) => {
    const files = [
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

  const loadAlwaysOnSkills = (cwd: string) => {
    // Skills that should be loaded on every session startup
    const alwaysOnSkills = [
      { path: ".pi/skills/context-engineering/SKILL.md", label: "Context Engineering" },
    ];

    const loaded: string[] = [];
    const contents: string[] = [];

    for (const skill of alwaysOnSkills) {
      const fullPath = path.join(cwd, skill.path);
      if (fs.existsSync(fullPath)) {
        const content = fs.readFileSync(fullPath, "utf-8");
        contents.push(`\n\n--- SKILL: ${skill.label.toUpperCase()} (${skill.path}) ---\n${content}`);
        loaded.push(skill.label);
      }
    }

    return { loaded, content: contents.join("\n") };
  };

  /**
   * Context Constructor — load persistent memory from context/ repository.
   * Runs the Constructor stage of the context-engineering pipeline:
   * reads facts, episodic summaries, and human annotations, assembles
   * a token-budgeted context payload, and returns it for injection.
   */
  const constructContext = (trust: WorkspaceTrust | null): { loaded: string[]; content: string } => {
    if (!trust) return { loaded: [], content: "" };
    const scriptPath = resolveTrustedWorkspaceScript(trust, "scripts/context_constructor.py");
    if (!scriptPath) {
      return { loaded: [], content: "" };
    }
    
    try {
      const output = execFileSync("python3.13", [scriptPath, "--json", "--budget", "8000"], {
        cwd: trust.root,
        encoding: "utf-8",
        timeout: 10000,
      });
      
      const result = JSON.parse(output);
      const context = result.context || "";
      const facts = result.facts_count || 0;
      const episodes = result.episodes_count || 0;
      const human = result.human_count || 0;
      const total = facts + episodes + human;
      
      if (total === 0 || !context) {
        return { loaded: [], content: "" };
      }
      
      const label = `Memory (${facts}F/${episodes}E/${human}H)`;
      const wrappedContent = `\n\n--- PERSISTENT MEMORY (${total} items, ${result.tokens_used} tokens) ---\n${context}\n\n---\nEND PERSISTENT MEMORY\n---`;
      
      return { loaded: [label], content: wrappedContent };
    } catch (e) {
      // Non-fatal — context repo may be empty or script may fail
      return { loaded: [], content: "" };
    }
  };

  // Inject docs, always-on skills, and persistent memory into system prompt context
  pi.on("before_agent_start", async (event, ctx) => {
    const docs = loadProjectDocs(ctx.cwd);
    const skills = loadAlwaysOnSkills(ctx.cwd);
    const memory = constructContext(createWorkspaceTrust(ctx.cwd));
    
    const allLoaded = [...docs.loaded, ...skills.loaded, ...memory.loaded];
    const allContent = [docs.content, skills.content].filter(Boolean).join("\n");
    const memoryContent = memory.content || "";
    
    if ((allContent || memoryContent) && allLoaded.length > 0) {
      const injectedPrompt = `
## PROJECT DOCUMENTATION (Auto-loaded)

${docs.content}

---
END PROJECT DOCUMENTATION
---

## ALWAYS-ON SKILLS (Auto-loaded)

${skills.content}

---
END ALWAYS-ON SKILLS
---
${memoryContent}
`;
      
      return {
        systemPrompt: event.systemPrompt + "\n" + injectedPrompt,
      };
    }
  });

  // Run IB reconciliation asynchronously (non-blocking)
  // onComplete callback is called when IB sync finishes (for chaining free trade)
  const runIBReconciliation = (trust: WorkspaceTrust, tracker: StartupTracker, onComplete?: () => void) => {
    const scriptPath = resolveTrustedWorkspaceScript(trust, "scripts/ib_reconcile.py");
    if (!scriptPath) {
      tracker.complete("ib", "warning", "IB reconcile script is not trusted");
      onComplete?.();
      return;
    }
    
    runBoundedStartupJob("python3.13", [scriptPath], trust.root, ({ code, stdout: output, stderr: errorOutput, timedOut }) => {
      if (timedOut) {
        tracker.complete("ib", "warning", "IB reconciliation timed out");
        onComplete?.();
        return;
      }
      if (code === 0) {
        // Check if reconciliation found issues
        const reconcilePath = path.join(trust.root, "data/reconciliation.json");
        if (fs.existsSync(reconcilePath)) {
          try {
            const report = JSON.parse(fs.readFileSync(reconcilePath, "utf-8"));
            if (report.needs_attention) {
              const newTrades = report.new_trades?.length || 0;
              const missingLocal = report.positions_missing_locally?.length || 0;
              const closed = report.positions_closed?.length || 0;
              
              const messages: string[] = [];
              if (newTrades > 0) messages.push(`${newTrades} new trades`);
              if (missingLocal > 0) messages.push(`${missingLocal} new positions`);
              if (closed > 0) messages.push(`${closed} closed positions`);
              
              tracker.complete("ib", "warning", `IB: ${messages.join(", ")}`);
            } else {
              tracker.complete("ib", "success", "IB trades in sync");
            }
          } catch (e) {
            tracker.complete("ib", "success", "IB reconciliation done");
          }
        } else {
          tracker.complete("ib", "success", "IB reconciliation done");
        }
      } else if (errorOutput.includes("IB connection failed") || errorOutput.includes("Cannot connect")) {
        tracker.complete("ib", "warning", "IB not connected (skipped)");
      } else if (errorOutput) {
        tracker.complete("ib", "error", `IB error: ${errorOutput.slice(0, 50)}`);
      } else {
        tracker.complete("ib", "warning", "IB reconciliation failed");
      }
      
      // Always call onComplete so dependent tasks can run
      onComplete?.();
    });
  };

  // Check and start Monitor Daemon service
  const checkMonitorDaemon = (cwd: string, ui: any): { running: boolean; error: string | null } => {
    const serviceName = "com.radon.monitor-daemon";
    const plistPath = path.join(process.env.HOME || "", "Library/LaunchAgents", `${serviceName}.plist`);
    
    // Check if plist is installed
    if (!fs.existsSync(plistPath)) {
      return { running: false, error: "Service not installed. Run: ./scripts/setup_monitor_daemon.sh install" };
    }
    
    try {
      // Check if service is running via launchctl
      const result = execFileSync("launchctl", ["list", serviceName], {
        encoding: "utf-8",
        timeout: 5000 
      }).trim();
      
      // launchctl list output: PID Status Label
      // If PID is "-" or "0", service is loaded but idle (normal for interval-based)
      // If we get a result, the service is loaded
      if (result.includes(serviceName)) {
        return { running: true, error: null };
      }
      
      return { running: false, error: null };
    } catch (e: any) {
      // grep returns exit code 1 if no match - service not loaded
      if (e.status === 1) {
        return { running: false, error: null };
      }
      return { running: false, error: e.message };
    }
  };
  
  const startMonitorDaemon = (trust: WorkspaceTrust): { success: boolean; error: string | null } => {
    const plistPath = path.join(process.env.HOME || "", "Library/LaunchAgents", "com.radon.monitor-daemon.plist");
    const configPath = resolveTrustedWorkspaceScript(trust, "config/com.radon.monitor-daemon.plist");
    
    // If plist not in LaunchAgents, copy it
    if (!fs.existsSync(plistPath)) {
      if (!configPath) {
        return { success: false, error: "Plist config is not trusted. Daemon not set up." };
      }
      
      try {
        // Copy plist to LaunchAgents
        fs.copyFileSync(configPath, plistPath);
      } catch (e: any) {
        return { success: false, error: `Failed to copy plist: ${e.message}` };
      }
    }
    
    try {
      // Load the service
      execFileSync("launchctl", ["load", plistPath], {
        encoding: "utf-8",
        timeout: 5000 
      });
      return { success: true, error: null };
    } catch (e: any) {
      // Already loaded is not an error
      if (e.message?.includes("already loaded")) {
        return { success: true, error: null };
      }
      return { success: false, error: e.message };
    }
  };
  
  const ensureMonitorDaemonRunning = (trust: WorkspaceTrust, tracker: StartupTracker) => {
    const status = checkMonitorDaemon(trust.root, tracker);
    
    if (status.running) {
      tracker.complete("daemon", "success", "Monitor daemon running");
      return;
    }
    
    if (status.error?.includes("not installed")) {
      tracker.complete("daemon", "warning", "Monitor daemon not installed");
      return;
    }
    
    // Try to start it
    const startResult = startMonitorDaemon(trust);
    
    if (startResult.success) {
      tracker.complete("daemon", "success", "Monitor daemon started");
    } else {
      tracker.complete("daemon", "error", `Daemon failed: ${startResult.error?.slice(0, 30)}`);
    }
  };

  // Run free trade analyzer asynchronously (non-blocking)
  const runFreeTradeAnalyzer = (trust: WorkspaceTrust, tracker: StartupTracker) => {
    const scriptPath = resolveTrustedWorkspaceScript(trust, "scripts/free_trade_analyzer.py");
    if (!scriptPath) {
      tracker.complete("free_trade", "warning", "Free trade script is not trusted");
      return;
    }
    
    runBoundedStartupJob("python3.13", [scriptPath, "--table"], trust.root, ({ code, stdout: output, stderr: errorOutput, timedOut }) => {
      if (timedOut) {
        tracker.complete("free_trade", "warning", "Free trade analysis timed out");
        return;
      }
      if (code === 0) {
        const result = output.trim();
        
        // Check if no positions found
        if (result.includes("No multi-leg positions")) {
          tracker.complete("free_trade", "success", "No multi-leg positions to analyze");
        } else {
          // Has positions - show the full table
          // Format: extract key info for notification
          const lines = result.split("\n");
          const dataLines = lines.filter(l => 
            l.trim() && 
            !l.startsWith("=") && 
            !l.startsWith("-") && 
            !l.startsWith("💰") &&
            !l.startsWith("Ticker") &&
            !l.startsWith("🎉 0")  // Skip "0 FREE" summary
          );
          
          // Build compact table for notification
          if (dataLines.length > 0) {
            // Extract just ticker + progress + status from each line
            const summaryLines = dataLines.map(line => {
              const parts = line.trim().split(/\s+/);
              if (parts.length >= 6) {
                const ticker = parts[0];
                // Find progress (ends with %)
                const progressIdx = parts.findIndex(p => p.endsWith("%"));
                const progress = progressIdx >= 0 ? parts[progressIdx] : "";
                // Status is usually last 1-2 tokens
                const statusParts = parts.slice(-2);
                const status = statusParts.join(" ");
                return `  ${ticker}: ${progress} ${status}`;
              }
              return null;
            }).filter(Boolean);
            
            if (summaryLines.length > 0) {
              const tableOutput = "Free Trade Progress:\n" + summaryLines.join("\n");
              tracker.complete("free_trade", "success", tableOutput);
            } else {
              tracker.complete("free_trade", "success", "No free trade opportunities");
            }
          } else {
            tracker.complete("free_trade", "success", "No free trade opportunities");
          }
        }
      } else {
        // Only notify on actual errors, not empty results
        if (errorOutput && !errorOutput.includes("No positions")) {
          tracker.complete("free_trade", "warning", summarizeFreeTradeError(errorOutput));
        } else {
          tracker.complete("free_trade", "success", "No positions to analyze");
        }
      }
    });
  };

  // Run CRI scan asynchronously to pre-warm data/cri.json for /regime page
  const runCriScan = (trust: WorkspaceTrust, tracker: StartupTracker) => {
    const scriptPath = resolveTrustedWorkspaceScript(trust, "scripts/cri_scan.py");
    const cachePath = path.join(trust.root, "data/cri.json");

    if (!scriptPath) {
      tracker.complete("cri", "warning", "CRI scan script is not trusted");
      return;
    }

    // Check if cache is fresh (< 30 min old) — skip scan if so
    if (fs.existsSync(cachePath)) {
      try {
        const stat = fs.statSync(cachePath);
        const ageMinutes = (Date.now() - stat.mtimeMs) / (1000 * 60);
        if (ageMinutes < 30) {
          tracker.complete("cri", "success", `CRI cache fresh (${Math.round(ageMinutes)}m old)`);
          return;
        }
      } catch {
        // Fall through to run scan
      }
    }

    runBoundedStartupJob("python3.13", [scriptPath, "--json"], trust.root, ({ code, stdout: output, stderr: errorOutput, timedOut }) => {
      if (timedOut) {
        tracker.complete("cri", "warning", "CRI scan timed out");
        return;
      }
      if (code === 0 && output.trim()) {
        // Write result to cache file for /regime page
        try {
          const jsonStart = output.indexOf("{");
          if (jsonStart >= 0) {
            const jsonStr = output.slice(jsonStart);
            const parsed = JSON.parse(jsonStr);
            const score = parsed?.cri?.score ?? "?";
            const level = parsed?.cri?.level ?? "?";
            fs.writeFileSync(cachePath, JSON.stringify(parsed, null, 2));
            tracker.complete("cri", "success", `CRI: ${score}/100 (${level})`);
          } else {
            tracker.complete("cri", "warning", "CRI scan: no JSON output");
          }
        } catch (e) {
          tracker.complete("cri", "warning", "CRI scan: parse error");
        }
      } else if (errorOutput.includes("client id is already in use") || errorOutput.includes("Cannot connect")) {
        tracker.complete("cri", "warning", "CRI: IB busy, skipped");
      } else {
        tracker.complete("cri", "warning", "CRI scan failed");
      }
    });
  };

  // Check X account scan status — reads from data/x_accounts.json
  const checkXScanStatus = (cwd: string): { account: string; needsScan: boolean; lastScan: string | null; hoursSince: number | null }[] => {
    const accountsPath = path.join(cwd, "data/x_accounts.json");
    const results: { account: string; needsScan: boolean; lastScan: string | null; hoursSince: number | null }[] = [];

    if (!fs.existsSync(accountsPath)) {
      return results;
    }

    try {
      const data = JSON.parse(fs.readFileSync(accountsPath, "utf-8"));
      const accounts = data.accounts || [];

      for (const entry of accounts) {
        if (!entry.username || entry.enabled === false) continue;

        const account = entry.username;
        const lastScan = entry.last_scan || null;

        // Check if scan is needed (more than 12 hours old or never scanned)
        let needsScan = !lastScan;
        let hoursSince: number | null = null;

        if (lastScan) {
          const lastScanDate = new Date(lastScan);
          const now = new Date();
          hoursSince = (now.getTime() - lastScanDate.getTime()) / (1000 * 60 * 60);
          needsScan = hoursSince > 12;
        }

        results.push({ account, needsScan, lastScan, hoursSince });
      }
    } catch (e) {
      // Ignore parse errors
    }

    return results;
  };

  // Update last_scan timestamp in x_accounts.json after successful scan
  const updateXAccountLastScan = (cwd: string, account: string) => {
    const accountsPath = path.join(cwd, "data/x_accounts.json");
    try {
      const data = JSON.parse(fs.readFileSync(accountsPath, "utf-8"));
      const entry = (data.accounts || []).find((a: any) => a.username === account);
      if (entry) {
        entry.last_scan = new Date().toISOString();
        fs.writeFileSync(accountsPath, JSON.stringify(data, null, 2));
      }
    } catch {
      // Non-fatal
    }
  };

  // Run X account scan asynchronously (non-blocking)
  const runXScan = (trust: WorkspaceTrust, account: string, tracker: StartupTracker) => {
    const scriptPath = resolveTrustedWorkspaceScript(trust, "scripts/fetch_x_watchlist.py");
    const processName = `x_${account}`;
    
    if (!scriptPath) {
      tracker.complete(processName, "error", `@${account} scan script is not trusted`);
      return;
    }
    
    runBoundedStartupJob("python3.13", [scriptPath, "--account", account], trust.root, ({ code, stdout: output, timedOut }) => {
      if (timedOut) {
        tracker.complete(processName, "warning", `@${account}: scan timed out`);
        return;
      }
      if (code === 0) {
        // Parse output to get summary - look for "Tweets with tickers: N"
        const tweetsMatch = output.match(/Tweets with tickers:\s*(\d+)/);
        const newMatch = output.match(/New tickers added:\s*([^\n]+)/);
        const updatedMatch = output.match(/Tickers updated:\s*([^\n]+)/);
        
        let tickerCount = tweetsMatch ? tweetsMatch[1] : "0";
        let message = `@${account}: ${tickerCount} tweets`;
        
        // Add new/updated counts if present
        if (newMatch) {
          const newTickers = newMatch[1].split(',').length;
          message += `, ${newTickers} new`;
        }
        if (updatedMatch && !updatedMatch[1].includes("No changes")) {
          const updatedTickers = updatedMatch[1].split(',').length;
          message += `, ${updatedTickers} updated`;
        }
        
        // Update last_scan timestamp in x_accounts.json
        updateXAccountLastScan(trust.root, account);
        tracker.complete(processName, "success", message);
      } else {
        // Check if it's a parsing issue (still ran, just no tickers)
        if (output.includes("No posts with tickers") || output.includes("No tweets with tickers")) {
          updateXAccountLastScan(trust.root, account);
          tracker.complete(processName, "success", `@${account}: 0 tweets`);
        } else {
          tracker.complete(processName, "warning", `@${account}: scan incomplete`);
        }
      }
    });
  };

  // Format hours ago as human-readable string
  const formatHoursAgo = (hours: number): string => {
    if (hours < 1) {
      const mins = Math.round(hours * 60);
      return `${mins}m ago`;
    } else if (hours < 24) {
      return `${hours.toFixed(1)}h ago`;
    } else {
      const days = Math.floor(hours / 24);
      return `${days}d ago`;
    }
  };

  // Notify on session start
  pi.on("session_start", async (_event, ctx) => {
    const docs = loadProjectDocs(ctx.cwd);
    const skills = loadAlwaysOnSkills(ctx.cwd);
    const trust = createWorkspaceTrust(ctx.cwd);
    const xScans = trust ? checkXScanStatus(trust.root) : [];
    const marketStatus = isMarketOpen();
    
    // Build list of processes to track
    // Order matters: IB sync must complete BEFORE free trade analysis
    // because closed positions affect which multi-leg positions exist
    const processNames: string[] = ["market", "docs", "ib", "free_trade", "daemon", "cri"];
    
    // Add ALL X accounts to process list (not just pending scans)
    for (const scan of xScans) {
      processNames.push(`x_${scan.account}`);
    }
    
    // Create tracker
    const tracker = new StartupTracker(ctx.ui, processNames);
    
    // Report market status first
    if (marketStatus.isOpen) {
      tracker.complete("market", "success", `Market ${marketStatus.status}`);
    } else {
      tracker.complete("market", "warning", `Market CLOSED (${marketStatus.status}) — using closing prices`);
    }
    
    // Load persistent memory from context/ repository (sync)
    const memory = constructContext(trust);
    
    // Complete docs immediately (sync)
    const allLoaded = [...docs.loaded, ...skills.loaded, ...memory.loaded];
    if (allLoaded.length > 0) {
      tracker.complete("docs", "success", `Loaded: ${allLoaded.join(", ")}`);
    } else {
      tracker.complete("docs", "warning", "No project docs found");
    }
    
    if (!trust) {
      tracker.complete("ib", "warning", "Startup automation disabled: workspace is not trusted");
      tracker.complete("free_trade", "warning", "Free trade analysis disabled: workspace is not trusted");
      tracker.complete("daemon", "warning", "Daemon startup disabled: workspace is not trusted");
      tracker.complete("cri", "warning", "CRI scan disabled: workspace is not trusted");
      return;
    }

    // Run IB reconciliation FIRST (async, but free trade waits for it)
    // Pass callback to run free trade after IB completes
    runIBReconciliation(trust, tracker, () => {
      // Run free trade AFTER IB sync completes (positions may have changed)
      runFreeTradeAnalyzer(trust, tracker);
    });
    
    // Check and ensure Monitor Daemon is running (sync)
    // This handles fill monitoring and exit order placement
    ensureMonitorDaemonRunning(trust, tracker);
    
    // Run CRI scan in parallel (pre-warm /regime page data)
    runCriScan(trust, tracker);

    // Run X account scans — only scan stale accounts (>12h), report fresh ones immediately
    for (const scan of xScans) {
      if (scan.needsScan) {
        runXScan(trust, scan.account, tracker);
      } else {
        const ago = scan.hoursSince != null ? formatHoursAgo(scan.hoursSince) : "recently";
        tracker.complete(`x_${scan.account}`, "success", `@${scan.account}: fresh (${ago})`);
      }
    }
  });
}
