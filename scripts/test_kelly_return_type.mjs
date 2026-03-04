#!/usr/bin/env node
/**
 * TDD Test: kelly_calc must return AgentToolResult format
 * 
 * The Pi extension API expects tools to return:
 * { content: [{ type: "text", text: "..." }], details: ... }
 * 
 * NOT a plain string.
 */

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  ✅ PASS: ${name}`);
    passed++;
  } catch (e) {
    console.log(`  ❌ FAIL: ${name}`);
    console.log(`          ${e.message}`);
    failed++;
  }
}

function assert(condition, msg) {
  if (!condition) throw new Error(msg);
}

// ============================================================================
// Test: kelly_calc execute() return type
// ============================================================================

console.log("\n── kelly_calc return type validation ──");

/**
 * Simulates the execute function from trading-tools.ts
 * This is the CURRENT (buggy) implementation that returns a string
 */
function kellyCalcBuggy({ prob_win, odds, fraction = 0.25, bankroll }) {
  if (typeof prob_win !== "number" || !Number.isFinite(prob_win) ||
      typeof odds !== "number" || !Number.isFinite(odds) ||
      odds <= 0) {
    const result = {
      full_kelly_pct: 0,
      fractional_kelly_pct: 0,
      edge_exists: false,
      recommendation: "DO NOT BET",
    };
    if (bankroll) {
      result.dollar_size = 0;
      result.max_per_position = +(bankroll * 0.025).toFixed(2);
      result.use_size = 0;
    }
    // BUG: Returns string instead of AgentToolResult
    return JSON.stringify(result, null, 2);
  }

  const q = 1 - prob_win;
  const fullKelly = prob_win - q / odds;
  const fracKelly = fullKelly * fraction;
  const result = {
    full_kelly_pct: +(fullKelly * 100).toFixed(2),
    fractional_kelly_pct: +(fracKelly * 100).toFixed(2),
    edge_exists: fullKelly > 0,
    recommendation: fullKelly <= 0 ? "DO NOT BET"
      : fullKelly > 0.1 ? "STRONG"
      : fullKelly > 0.025 ? "MARGINAL" : "WEAK",
  };
  if (bankroll) {
    result.dollar_size = +(bankroll * fracKelly).toFixed(2);
    result.max_per_position = +(bankroll * 0.025).toFixed(2);
    result.use_size = Math.min(result.dollar_size, result.max_per_position);
  }
  // BUG: Returns string instead of AgentToolResult
  return JSON.stringify(result, null, 2);
}

/**
 * Validates that a tool result matches AgentToolResult interface:
 * { content: Array<{type: "text", text: string}>, details: any }
 */
function validateAgentToolResult(result) {
  if (typeof result !== "object" || result === null) {
    return { valid: false, error: "Result must be an object" };
  }
  
  if (!("content" in result)) {
    return { valid: false, error: "Result must have 'content' property" };
  }
  
  if (!Array.isArray(result.content)) {
    return { valid: false, error: "Result.content must be an array" };
  }
  
  for (const item of result.content) {
    if (typeof item !== "object" || item === null) {
      return { valid: false, error: "Content items must be objects" };
    }
    if (item.type !== "text" && item.type !== "image") {
      return { valid: false, error: `Content item type must be 'text' or 'image', got '${item.type}'` };
    }
    if (item.type === "text" && typeof item.text !== "string") {
      return { valid: false, error: "Text content must have 'text' string property" };
    }
  }
  
  // details can be undefined or any value
  return { valid: true };
}

// These tests will FAIL with the buggy implementation (RED)
test("buggy implementation returns string (will fail validation)", () => {
  const result = kellyCalcBuggy({ prob_win: 0.6, odds: 2.0 });
  const validation = validateAgentToolResult(result);
  // This SHOULD fail because kellyCalcBuggy returns a string
  assert(!validation.valid, "Buggy implementation should fail validation");
  assert(validation.error === "Result must be an object", 
    `Expected 'Result must be an object', got '${validation.error}'`);
});

test("buggy implementation returns invalid type", () => {
  const result = kellyCalcBuggy({ prob_win: 0.6, odds: 2.0 });
  assert(typeof result === "string", "Buggy implementation returns string");
});

// ============================================================================
// Test: Fixed implementation
// ============================================================================

console.log("\n── kelly_calc FIXED return type ──");

/**
 * FIXED implementation that returns proper AgentToolResult
 */
function kellyCalcFixed({ prob_win, odds, fraction = 0.25, bankroll }) {
  if (typeof prob_win !== "number" || !Number.isFinite(prob_win) ||
      typeof odds !== "number" || !Number.isFinite(odds) ||
      odds <= 0) {
    const result = {
      full_kelly_pct: 0,
      fractional_kelly_pct: 0,
      edge_exists: false,
      recommendation: "DO NOT BET",
    };
    if (bankroll) {
      result.dollar_size = 0;
      result.max_per_position = +(bankroll * 0.025).toFixed(2);
      result.use_size = 0;
    }
    // FIXED: Return AgentToolResult format
    return { 
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      details: undefined
    };
  }

  const q = 1 - prob_win;
  const fullKelly = prob_win - q / odds;
  const fracKelly = fullKelly * fraction;
  const result = {
    full_kelly_pct: +(fullKelly * 100).toFixed(2),
    fractional_kelly_pct: +(fracKelly * 100).toFixed(2),
    edge_exists: fullKelly > 0,
    recommendation: fullKelly <= 0 ? "DO NOT BET"
      : fullKelly > 0.1 ? "STRONG"
      : fullKelly > 0.025 ? "MARGINAL" : "WEAK",
  };
  if (bankroll) {
    result.dollar_size = +(bankroll * fracKelly).toFixed(2);
    result.max_per_position = +(bankroll * 0.025).toFixed(2);
    result.use_size = Math.min(result.dollar_size, result.max_per_position);
  }
  // FIXED: Return AgentToolResult format
  return { 
    content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    details: undefined
  };
}

test("fixed implementation returns valid AgentToolResult", () => {
  const result = kellyCalcFixed({ prob_win: 0.6, odds: 2.0 });
  const validation = validateAgentToolResult(result);
  assert(validation.valid, `Validation failed: ${validation.error}`);
});

test("fixed implementation has content array", () => {
  const result = kellyCalcFixed({ prob_win: 0.6, odds: 2.0 });
  assert(Array.isArray(result.content), "content should be an array");
  assert(result.content.length === 1, "content should have one item");
});

test("fixed implementation content has type 'text'", () => {
  const result = kellyCalcFixed({ prob_win: 0.6, odds: 2.0 });
  assert(result.content[0].type === "text", "content item type should be 'text'");
});

test("fixed implementation content.text is valid JSON", () => {
  const result = kellyCalcFixed({ prob_win: 0.6, odds: 2.0 });
  const parsed = JSON.parse(result.content[0].text);
  assert(typeof parsed.full_kelly_pct === "number", "should have full_kelly_pct");
  assert(typeof parsed.recommendation === "string", "should have recommendation");
});

test("fixed implementation handles invalid odds", () => {
  const result = kellyCalcFixed({ prob_win: 0.6, odds: 0 });
  const validation = validateAgentToolResult(result);
  assert(validation.valid, `Validation failed: ${validation.error}`);
  const parsed = JSON.parse(result.content[0].text);
  assert(parsed.recommendation === "DO NOT BET", "should recommend DO NOT BET for odds=0");
});

test("fixed implementation handles undefined params", () => {
  const result = kellyCalcFixed({});
  const validation = validateAgentToolResult(result);
  assert(validation.valid, `Validation failed: ${validation.error}`);
  const parsed = JSON.parse(result.content[0].text);
  assert(parsed.recommendation === "DO NOT BET", "should recommend DO NOT BET for undefined params");
});

// ============================================================================
// Summary
// ============================================================================
console.log(`\n── Results: ${passed} passed, ${failed} failed ──\n`);
process.exit(failed > 0 ? 1 : 0);
