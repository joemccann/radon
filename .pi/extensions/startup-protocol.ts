import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";
import { execSync, spawn } from "node:child_process";

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
function isMarketOpen(): { isOpen: boolean; status: string } {
  const now = new Date();
  
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
  const hour = parseInt(parts.find(p => p.type === 'hour')?.value || '0', 10);
  const minute = parseInt(parts.find(p => p.type === 'minute')?.value || '0', 10);
  const timeInMinutes = hour * 60 + minute;
  
  // Market hours: 9:30 AM (570 mins) to 4:00 PM (960 mins)
  const marketOpen = 9 * 60 + 30;  // 570
  const marketClose = 16 * 60;      // 960
  
  // Check weekend
  if (weekday === 'Sat' || weekday === 'Sun') {
    return { isOpen: false, status: 'CLOSED (weekend)' };
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
  const constructContext = (cwd: string): { loaded: string[]; content: string } => {
    const scriptPath = path.join(cwd, "scripts/context_constructor.py");
    
    if (!fs.existsSync(scriptPath)) {
      return { loaded: [], content: "" };
    }
    
    try {
      const output = execSync(`python3 "${scriptPath}" --json --budget 8000`, {
        cwd,
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
    const memory = constructContext(ctx.cwd);
    
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
  const runIBReconciliation = (cwd: string, tracker: StartupTracker, onComplete?: () => void) => {
    const scriptPath = path.join(cwd, "scripts/ib_reconcile.py");
    
    if (!fs.existsSync(scriptPath)) {
      tracker.complete("ib", "warning", "IB reconcile script not found");
      onComplete?.();
      return;
    }
    
    // Spawn Python process in background
    const proc = spawn("python3", [scriptPath], {
      cwd,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    
    let output = "";
    let errorOutput = "";
    
    proc.stdout?.on("data", (data) => {
      output += data.toString();
    });
    
    proc.stderr?.on("data", (data) => {
      errorOutput += data.toString();
    });
    
    proc.on("close", (code) => {
      if (code === 0) {
        // Check if reconciliation found issues
        const reconcilePath = path.join(cwd, "data/reconciliation.json");
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
    
    // Unref so it doesn't keep the process alive
    proc.unref();
  };

  // Check and start Monitor Daemon service
  const checkMonitorDaemon = (cwd: string, ui: any): { running: boolean; error: string | null } => {
    const serviceName = "com.convex-scavenger.monitor-daemon";
    const plistPath = path.join(process.env.HOME || "", "Library/LaunchAgents", `${serviceName}.plist`);
    
    // Check if plist is installed
    if (!fs.existsSync(plistPath)) {
      return { running: false, error: "Service not installed. Run: ./scripts/setup_monitor_daemon.sh install" };
    }
    
    try {
      // Check if service is running via launchctl
      const result = execSync(`launchctl list | grep ${serviceName}`, { 
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
  
  const startMonitorDaemon = (cwd: string, ui: any): { success: boolean; error: string | null } => {
    const plistPath = path.join(process.env.HOME || "", "Library/LaunchAgents", "com.convex-scavenger.monitor-daemon.plist");
    const configPath = path.join(cwd, "config/com.convex-scavenger.monitor-daemon.plist");
    
    // If plist not in LaunchAgents, copy it
    if (!fs.existsSync(plistPath)) {
      if (!fs.existsSync(configPath)) {
        return { success: false, error: "Plist config not found. Daemon not set up." };
      }
      
      try {
        // Copy plist to LaunchAgents
        execSync(`cp "${configPath}" "${plistPath}"`, { timeout: 5000 });
      } catch (e: any) {
        return { success: false, error: `Failed to copy plist: ${e.message}` };
      }
    }
    
    try {
      // Load the service
      execSync(`launchctl load "${plistPath}"`, { 
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
  
  const ensureMonitorDaemonRunning = (cwd: string, tracker: StartupTracker) => {
    const status = checkMonitorDaemon(cwd, tracker);
    
    if (status.running) {
      tracker.complete("daemon", "success", "Monitor daemon running");
      return;
    }
    
    if (status.error?.includes("not installed")) {
      tracker.complete("daemon", "warning", "Monitor daemon not installed");
      return;
    }
    
    // Try to start it
    const startResult = startMonitorDaemon(cwd, tracker);
    
    if (startResult.success) {
      tracker.complete("daemon", "success", "Monitor daemon started");
    } else {
      tracker.complete("daemon", "error", `Daemon failed: ${startResult.error?.slice(0, 30)}`);
    }
  };

  // Run free trade analyzer asynchronously (non-blocking)
  const runFreeTradeAnalyzer = (cwd: string, tracker: StartupTracker) => {
    const scriptPath = path.join(cwd, "scripts/free_trade_analyzer.py");
    
    if (!fs.existsSync(scriptPath)) {
      tracker.complete("free_trade", "warning", "Free trade script not found");
      return;
    }
    
    // Spawn Python process in background with --table for full table output
    const proc = spawn("python3", [scriptPath, "--table"], {
      cwd,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    
    let output = "";
    let errorOutput = "";
    
    proc.stdout?.on("data", (data) => {
      output += data.toString();
    });
    
    proc.stderr?.on("data", (data) => {
      errorOutput += data.toString();
    });
    
    proc.on("close", (code) => {
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
          tracker.complete("free_trade", "warning", "Free trade analysis error");
        } else {
          tracker.complete("free_trade", "success", "No positions to analyze");
        }
      }
    });
    
    // Unref so it doesn't keep the process alive
    proc.unref();
  };

  // Run CRI scan asynchronously to pre-warm data/cri.json for /regime page
  const runCriScan = (cwd: string, tracker: StartupTracker) => {
    const scriptPath = path.join(cwd, "scripts/cri_scan.py");
    const cachePath = path.join(cwd, "data/cri.json");

    if (!fs.existsSync(scriptPath)) {
      tracker.complete("cri", "warning", "CRI scan script not found");
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

    // Spawn cri_scan.py --json in background
    const proc = spawn("python3", [scriptPath, "--json"], {
      cwd,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let output = "";
    let errorOutput = "";

    proc.stdout?.on("data", (data) => {
      output += data.toString();
    });

    proc.stderr?.on("data", (data) => {
      errorOutput += data.toString();
    });

    proc.on("close", (code) => {
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

    proc.unref();
  };

  // Check X account scan status
  const checkXScanStatus = (cwd: string): { account: string; needsScan: boolean; lastScan: string | null; hoursSince: number | null }[] => {
    const watchlistPath = path.join(cwd, "data/watchlist.json");
    const results: { account: string; needsScan: boolean; lastScan: string | null; hoursSince: number | null }[] = [];
    
    if (!fs.existsSync(watchlistPath)) {
      return results;
    }
    
    try {
      const watchlist = JSON.parse(fs.readFileSync(watchlistPath, "utf-8"));
      const subcategories = watchlist.subcategories || {};
      
      for (const [key, value] of Object.entries(subcategories)) {
        if (key.startsWith("@")) {
          const account = key.slice(1);
          const lastScan = (value as any).last_scan || null;
          
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
      }
    } catch (e) {
      // Ignore parse errors
    }
    
    return results;
  };

  // Run X account scan asynchronously (non-blocking)
  const runXScan = (cwd: string, account: string, tracker: StartupTracker) => {
    const scriptPath = path.join(cwd, "scripts/fetch_x_watchlist.py");
    const processName = `x_${account}`;
    
    if (!fs.existsSync(scriptPath)) {
      tracker.complete(processName, "error", `@${account} scan script not found`);
      return;
    }
    
    // Spawn Python process in background
    const proc = spawn("python3", [scriptPath, "--account", account], {
      cwd,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    
    let output = "";
    let errorOutput = "";
    
    proc.stdout?.on("data", (data) => {
      output += data.toString();
    });
    
    proc.stderr?.on("data", (data) => {
      errorOutput += data.toString();
    });
    
    proc.on("close", (code) => {
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
        
        tracker.complete(processName, "success", message);
      } else {
        // Check if it's a parsing issue (still ran, just no tickers)
        if (output.includes("No posts with tickers") || output.includes("No tweets with tickers")) {
          tracker.complete(processName, "success", `@${account}: 0 tweets`);
        } else {
          tracker.complete(processName, "warning", `@${account}: scan incomplete`);
        }
      }
    });
    
    // Unref so it doesn't keep the process alive
    proc.unref();
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
    const xScans = checkXScanStatus(ctx.cwd);
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
    const memory = constructContext(ctx.cwd);
    
    // Complete docs immediately (sync)
    const allLoaded = [...docs.loaded, ...skills.loaded, ...memory.loaded];
    if (allLoaded.length > 0) {
      tracker.complete("docs", "success", `Loaded: ${allLoaded.join(", ")}`);
    } else {
      tracker.complete("docs", "warning", "No project docs found");
    }
    
    // Run IB reconciliation FIRST (async, but free trade waits for it)
    // Pass callback to run free trade after IB completes
    runIBReconciliation(ctx.cwd, tracker, () => {
      // Run free trade AFTER IB sync completes (positions may have changed)
      runFreeTradeAnalyzer(ctx.cwd, tracker);
    });
    
    // Check and ensure Monitor Daemon is running (sync)
    // This handles fill monitoring and exit order placement
    ensureMonitorDaemonRunning(ctx.cwd, tracker);
    
    // Run CRI scan in parallel (pre-warm /regime page data)
    runCriScan(ctx.cwd, tracker);

    // Run X account scans in parallel (independent of IB/free trade)
    for (const scan of xScans) {
      runXScan(ctx.cwd, scan.account, tracker);
    }
  });
}
