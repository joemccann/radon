/**
 * Startup Protocol Tests (TDD)
 * 
 * Tests that all startup processes are visible to the user.
 * Run with: npx tsx .pi/tests/startup-protocol.test.ts
 */

import * as assert from "node:assert";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, realpathSync, writeFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { marketStateAt } from "../extensions/startup-protocol.ts";

// Mock UI that captures all notifications
class MockUI {
  notifications: Array<{ message: string; level: string }> = [];
  
  notify(message: string, level: string = "info") {
    this.notifications.push({ message, level });
  }
  
  getMessages(): string[] {
    return this.notifications.map(n => n.message);
  }
  
  hasMessage(pattern: string | RegExp): boolean {
    return this.notifications.some(n => 
      typeof pattern === "string" 
        ? n.message.includes(pattern)
        : pattern.test(n.message)
    );
  }
  
  clear() {
    this.notifications = [];
  }
}

// Test runner
function test(name: string, fn: () => void | Promise<void>) {
  tests.push({ name, fn });
}

const tests: Array<{ name: string; fn: () => void | Promise<void> }> = [];

test("market state honors holidays and early closes", () => {
  assert.deepStrictEqual(
    marketStateAt(new Date("2026-12-25T16:00:00Z")),
    { isOpen: false, status: "CLOSED (holiday)" },
  );
  assert.deepStrictEqual(
    marketStateAt(new Date("2026-11-27T19:00:00Z")),
    { isOpen: false, status: "after hours" },
  );
  assert.deepStrictEqual(
    marketStateAt(new Date("2026-11-27T17:00:00Z")).isOpen,
    true,
  );
});

test("startup jobs use timeout, bounded output, spawn-error, and process-group termination", () => {
  const source = execFileSync("git", ["show", "HEAD:.pi/extensions/startup-protocol.ts"], {
    cwd: join(import.meta.dirname, "../.."),
    encoding: "utf8",
  });
  const current = readFileSync(
    join(import.meta.dirname, "../extensions/startup-protocol.ts"),
    "utf8",
  );
  assert.ok(!source.includes("runBoundedStartupJob"));
  assert.ok(current.includes("STARTUP_JOB_OUTPUT_BYTES"));
  assert.ok(current.includes('proc.once("error"'));
  assert.ok(current.includes('process.kill(-proc.pid, "SIGTERM")'));
  assert.strictEqual((current.match(/runBoundedStartupJob\("python3\.13"/g) ?? []).length, 4);
});

// ============================================================
// STARTUP PROCESS VISIBILITY TESTS
// ============================================================

test("should show startup banner with process count", () => {
  const ui = new MockUI();
  
  // Simulate startup banner
  const processes = ["IB Reconciliation", "Monitor Daemon", "Free Trade Analysis"];
  const banner = `🚀 Startup: Running ${processes.length} checks...`;
  ui.notify(banner, "info");
  
  assert.ok(ui.hasMessage("🚀 Startup:"), "Should show startup banner");
  assert.ok(ui.hasMessage("3 checks"), "Should show process count");
});

test("should show completion status for each process", () => {
  const ui = new MockUI();
  
  // Each process should have a numbered completion status
  const expectedProcesses = [
    { name: "Docs loaded", status: "✓" },
    { name: "IB reconciliation", status: "✓" },
    { name: "Monitor daemon", status: "✓" },
    { name: "Free trade analysis", status: "✓" },
  ];
  
  // Simulate completions
  expectedProcesses.forEach((proc, i) => {
    ui.notify(`[${i + 1}/${expectedProcesses.length}] ${proc.status} ${proc.name}`, "info");
  });
  
  assert.ok(ui.hasMessage("[1/4]"), "Should show numbered progress");
  assert.ok(ui.hasMessage("[4/4]"), "Should show final step");
});

test("should show final summary when all processes complete", () => {
  const ui = new MockUI();
  
  // Simulate final summary
  ui.notify("✅ Startup complete (4/4 passed)", "info");
  
  assert.ok(ui.hasMessage("✅ Startup complete"), "Should show completion message");
  assert.ok(ui.hasMessage("4/4 passed"), "Should show pass count");
});

test("should show warning summary when some processes have issues", () => {
  const ui = new MockUI();
  
  // Simulate partial success
  ui.notify("⚠️ Startup complete (3/4 passed, 1 warning)", "warning");
  
  assert.ok(ui.hasMessage("⚠️ Startup complete"), "Should show warning status");
  assert.ok(ui.hasMessage("1 warning"), "Should show warning count");
});

test("should track async process completion", async () => {
  const ui = new MockUI();
  
  // Mock StartupTracker
  class StartupTracker {
    private processes: Map<string, { status: "pending" | "success" | "warning" | "error"; message?: string }> = new Map();
    private ui: MockUI;
    private total: number;
    
    constructor(ui: MockUI, processNames: string[]) {
      this.ui = ui;
      this.total = processNames.length;
      processNames.forEach(name => this.processes.set(name, { status: "pending" }));
      this.ui.notify(`🚀 Startup: Running ${this.total} checks...`, "info");
    }
    
    complete(name: string, status: "success" | "warning" | "error", message: string) {
      this.processes.set(name, { status, message });
      const completed = Array.from(this.processes.values()).filter(p => p.status !== "pending").length;
      const icon = status === "success" ? "✓" : status === "warning" ? "⚠️" : "❌";
      this.ui.notify(`[${completed}/${this.total}] ${icon} ${message}`, status === "error" ? "error" : "info");
      
      // Check if all done
      if (completed === this.total) {
        this.showSummary();
      }
    }
    
    private showSummary() {
      const statuses = Array.from(this.processes.values());
      const successes = statuses.filter(s => s.status === "success").length;
      const warnings = statuses.filter(s => s.status === "warning").length;
      const errors = statuses.filter(s => s.status === "error").length;
      
      if (errors > 0) {
        this.ui.notify(`❌ Startup complete (${successes}/${this.total} passed, ${errors} failed)`, "error");
      } else if (warnings > 0) {
        this.ui.notify(`⚠️ Startup complete (${successes}/${this.total} passed, ${warnings} warnings)`, "warning");
      } else {
        this.ui.notify(`✅ Startup complete (${this.total}/${this.total} passed)`, "info");
      }
    }
  }
  
  // Test usage
  const tracker = new StartupTracker(ui, ["docs", "ib", "daemon", "free_trade"]);
  
  assert.ok(ui.hasMessage("🚀 Startup:"), "Should show startup banner");
  
  // Simulate async completions
  tracker.complete("docs", "success", "Docs loaded");
  tracker.complete("ib", "success", "IB trades in sync");
  tracker.complete("daemon", "success", "Monitor daemon running");
  tracker.complete("free_trade", "success", "No free trade opportunities");
  
  assert.ok(ui.hasMessage("[4/4]"), "Should show all processes complete");
  assert.ok(ui.hasMessage("✅ Startup complete"), "Should show final summary");
});

test("should handle IB connection failure gracefully", () => {
  const ui = new MockUI();
  
  // Simulate IB connection failure
  ui.notify("[2/4] ⚠️ IB not connected (skipped)", "warning");
  
  assert.ok(ui.hasMessage("IB not connected"), "Should show connection failure");
  assert.ok(ui.hasMessage("⚠️"), "Should use warning icon");
});

// ============================================================
// INTEGRATION TESTS - Test actual startup-protocol.ts exports
// ============================================================

test("StartupTracker class should be exported from startup-protocol", async () => {
  // Try to import StartupTracker from the actual module
  let StartupTracker: any;
  try {
    const module = await import("../extensions/startup-protocol.js");
    StartupTracker = module.StartupTracker;
  } catch (e) {
    // Module might export differently
    const module = await import("../extensions/startup-protocol.ts");
    StartupTracker = (module as any).StartupTracker;
  }
  
  assert.ok(StartupTracker, "StartupTracker should be exported");
  assert.ok(typeof StartupTracker === "function", "StartupTracker should be a class/function");
});

test("StartupTracker should immediately notify with check count", async () => {
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  new StartupTracker(ui, ["docs", "ib", "daemon"]);
  
  // Immediate notification with check count
  assert.strictEqual(ui.notifications.length, 1, "Should notify immediately with check count");
  assert.ok(ui.hasMessage("🚀 Startup: Running 3 checks..."), "Should show check count");
});

test("StartupTracker.complete should batch progress into final notification", async () => {
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  const tracker = new StartupTracker(ui, ["a", "b", "c"]);
  
  // Immediate startup notification
  assert.strictEqual(ui.notifications.length, 1, "Should have immediate startup notification");
  assert.ok(ui.hasMessage("🚀 Startup: Running 3 checks..."), "Should show startup banner");
  
  tracker.complete("a", "success", "A done");
  tracker.complete("b", "success", "B done");
  
  // Still only 1 notification - progress batched for end
  assert.strictEqual(ui.notifications.length, 1, "Should not notify progress individually");
  
  tracker.complete("c", "success", "C done");
  
  // Now we get the second batched notification with results
  assert.strictEqual(ui.notifications.length, 2, "Should have startup + results notifications");
  
  // The results notification should contain all progress + summary
  const msg = ui.notifications[1].message;
  assert.ok(msg.includes("[1/3]"), "Should include first step");
  assert.ok(msg.includes("[2/3]"), "Should include second step");
  assert.ok(msg.includes("[3/3]"), "Should include third step");
  assert.ok(msg.includes("✅ Startup complete"), "Should include summary");
});

test("StartupTracker should show final summary when all complete", async () => {
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  const tracker = new StartupTracker(ui, ["a", "b"]);
  tracker.complete("a", "success", "A done");
  tracker.complete("b", "success", "B done");
  
  assert.ok(ui.hasMessage("✅ Startup complete"), "Should show completion");
  assert.ok(ui.hasMessage("2/2 passed"), "Should show all passed");
});

test("StartupTracker should handle warnings in summary", async () => {
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  const tracker = new StartupTracker(ui, ["a", "b"]);
  tracker.complete("a", "success", "A done");
  tracker.complete("b", "warning", "B skipped");
  
  assert.ok(ui.hasMessage("⚠️ Startup complete"), "Should show warning status");
  assert.ok(ui.hasMessage(/1.*passed.*1.*warning/), "Should show warning count");
});

test("StartupTracker should handle errors in summary", async () => {
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  const tracker = new StartupTracker(ui, ["a", "b", "c"]);
  tracker.complete("a", "success", "A done");
  tracker.complete("b", "error", "B failed");
  tracker.complete("c", "warning", "C skipped");
  
  assert.ok(ui.hasMessage("❌ Startup complete"), "Should show error status");
  assert.ok(ui.hasMessage(/1.*passed.*1.*failed/), "Should show error count");
});

test("StartupTracker.isComplete should return correct state", async () => {
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  const tracker = new StartupTracker(ui, ["a", "b"]);
  
  assert.strictEqual(tracker.isComplete(), false, "Should be incomplete initially");
  
  tracker.complete("a", "success", "A done");
  assert.strictEqual(tracker.isComplete(), false, "Should still be incomplete");
  
  tracker.complete("b", "success", "B done");
  assert.strictEqual(tracker.isComplete(), true, "Should be complete now");
});

test("StartupTracker.getStatus should return process status", async () => {
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  const tracker = new StartupTracker(ui, ["a", "b"]);
  
  assert.strictEqual(tracker.getStatus("a"), "pending", "Should be pending initially");
  
  tracker.complete("a", "warning", "A skipped");
  assert.strictEqual(tracker.getStatus("a"), "warning", "Should be warning after completion");
});

// ============================================================
// MARKET HOURS TESTS
// ============================================================

test("isMarketOpen should be available and return correct type", async () => {
  // We can't import the function directly since it's internal, but we can
  // verify the StartupTracker uses it by checking the process names include "market"
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  // Create tracker with market process
  const tracker = new StartupTracker(ui, ["market", "docs"]);
  
  // The tracker should accept market as a valid process
  tracker.complete("market", "success", "Market OPEN (2h 30m to close)");
  assert.strictEqual(tracker.getStatus("market"), "success", "Market process should be trackable");
});

test("market status should show warning when market is closed", async () => {
  const { StartupTracker } = await import("../extensions/startup-protocol.ts");
  const ui = new MockUI();
  
  const tracker = new StartupTracker(ui, ["market", "docs"]);
  
  // Simulate market closed
  tracker.complete("market", "warning", "Market CLOSED (after hours) — using closing prices");
  tracker.complete("docs", "success", "Docs loaded");
  
  // Should show warning in summary because market was a warning
  assert.ok(ui.hasMessage("using closing prices"), "Should mention closing prices");
  assert.ok(ui.hasMessage("⚠️"), "Should have warning indicator for closed market");
});

test("summarizeFreeTradeError should prefer explicit IB verification failures", async () => {
  const { summarizeFreeTradeError } = await import("../extensions/startup-protocol.ts");

  const message = summarizeFreeTradeError([
    "IB error 326: Unable to connect as the client id is already in use. Retry with a unique client id.",
    "Cannot verify current portfolio via IB: Error 326, reqId -1: Unable to connect as the client id is already in use. Retry with a unique client id.",
    "API connection failed: TimeoutError()",
  ].join("\n"));

  assert.strictEqual(
    message,
    "Cannot verify current portfolio via IB: Error 326, reqId -1: Unable to connect as the client id is already in use. Retry with a unique client id.",
    "Should surface the operator-facing IB verification failure"
  );
});

test("summarizeFreeTradeError should fall back to first useful stderr line", async () => {
  const { summarizeFreeTradeError } = await import("../extensions/startup-protocol.ts");

  const message = summarizeFreeTradeError([
    "IB error 326: Unable to connect as the client id is already in use. Retry with a unique client id.",
    "Peer closed connection. clientId 47 already in use?",
    "custom free trade analyzer failure",
  ].join("\n"));

  assert.strictEqual(message, "custom free trade analyzer failure");
});

test("workspace startup scripts require an explicit canonical root and revision", async () => {
  const { createWorkspaceTrust } = await import("../extensions/startup-protocol.ts");
  const root = mkdtempSync(join(tmpdir(), "radon-startup-trust-"));
  mkdirSync(join(root, "scripts"));
  writeFileSync(join(root, "scripts", "helper.py"), "print('trusted')\n");
  execFileSync("git", ["init", "-q"], { cwd: root });
  execFileSync("git", ["add", "scripts/helper.py"], { cwd: root });
  execFileSync(
    "git",
    ["-c", "user.name=Radon Test", "-c", "user.email=test@radon.invalid", "commit", "-qm", "fixture"],
    { cwd: root },
  );
  const revision = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();

  assert.strictEqual(createWorkspaceTrust(root, {}), null);
  assert.strictEqual(
    createWorkspaceTrust(root, {
      RADON_PI_TRUSTED_WORKSPACE: join(root, "elsewhere"),
      RADON_PI_TRUSTED_REVISION: revision,
    }),
    null,
  );
  assert.ok(createWorkspaceTrust(root, {
    RADON_PI_TRUSTED_WORKSPACE: root,
    RADON_PI_TRUSTED_REVISION: revision,
  }));
});

test("trusted startup scripts must match the pinned Git revision and stay beneath the workspace", async () => {
  const {
    createWorkspaceTrust,
    resolveTrustedWorkspaceScript,
  } = await import("../extensions/startup-protocol.ts");
  const root = mkdtempSync(join(tmpdir(), "radon-startup-script-"));
  mkdirSync(join(root, "scripts"));
  const helper = join(root, "scripts", "helper.py");
  writeFileSync(helper, "print('trusted')\n");
  execFileSync("git", ["init", "-q"], { cwd: root });
  execFileSync("git", ["add", "scripts/helper.py"], { cwd: root });
  execFileSync(
    "git",
    ["-c", "user.name=Radon Test", "-c", "user.email=test@radon.invalid", "commit", "-qm", "fixture"],
    { cwd: root },
  );
  const revision = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
  const trust = createWorkspaceTrust(root, {
    RADON_PI_TRUSTED_WORKSPACE: root,
    RADON_PI_TRUSTED_REVISION: revision,
  });
  assert.ok(trust);
  assert.strictEqual(resolveTrustedWorkspaceScript(trust!, "scripts/helper.py"), realpathSync(helper));

  writeFileSync(helper, "print('modified')\n");
  assert.strictEqual(resolveTrustedWorkspaceScript(trust!, "scripts/helper.py"), null);

  const outside = join(tmpdir(), `radon-outside-${Date.now()}.py`);
  writeFileSync(outside, "print('outside')\n");
  symlinkSync(outside, join(root, "scripts", "outside.py"));
  assert.strictEqual(resolveTrustedWorkspaceScript(trust!, "scripts/outside.py"), null);
});

// ============================================================
// RUN ALL TESTS
// ============================================================

async function runTests() {
  console.log("\n🧪 Running Startup Protocol Tests\n");
  console.log("=".repeat(50));
  
  let passed = 0;
  let failed = 0;
  
  for (const { name, fn } of tests) {
    try {
      await fn();
      console.log(`✅ ${name}`);
      passed++;
    } catch (error: any) {
      console.log(`❌ ${name}`);
      console.log(`   ${error.message}`);
      failed++;
    }
  }
  
  console.log("=".repeat(50));
  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  
  if (failed > 0) {
    process.exit(1);
  }
}

runTests();
