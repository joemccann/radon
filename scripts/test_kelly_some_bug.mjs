#!/usr/bin/env node
/**
 * Red/Green TDD for the '.some' crash bug:
 *   Error: Cannot read properties of undefined (reading 'some')
 *
 * Root cause: kelly_calc's toolResult message ends up with content: undefined.
 * When the Anthropic provider calls convertContentBlocks(msg.content),
 * it crashes on content.some(...) because content is undefined.
 *
 * This test suite:
 *   1. Reproduces the exact crash (convertContentBlocks with undefined)
 *   2. Tests that kelly_calc's execute always returns valid AgentToolResult
 *   3. Tests the patched convertContentBlocks handles undefined gracefully
 *
 * Run: node scripts/test_kelly_some_bug.mjs
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
// 1. Reproduce the exact crash: convertContentBlocks(undefined)
// ============================================================================

/**
 * This is the UNPATCHED convertContentBlocks from pi-ai anthropic.js.
 * It crashes when content is undefined.
 */
function convertContentBlocks_UNPATCHED(content) {
  const hasImages = content.some((c) => c.type === "image");
  if (!hasImages) {
    return content.map((c) => c.text).join("\n");
  }
  return content.map((block) => {
    if (block.type === "text") return { type: "text", text: block.text };
    return { type: "image", source: { type: "base64", media_type: block.mimeType, data: block.data } };
  });
}

/**
 * PATCHED version that handles undefined/null content.
 */
function convertContentBlocks_PATCHED(content) {
  if (!content || !Array.isArray(content)) {
    return "No content provided";
  }
  const hasImages = content.some((c) => c.type === "image");
  if (!hasImages) {
    return content.map((c) => c.text).join("\n");
  }
  return content.map((block) => {
    if (block.type === "text") return { type: "text", text: block.text };
    return { type: "image", source: { type: "base64", media_type: block.mimeType, data: block.data } };
  });
}

console.log("\n── 1. Reproduce the crash (convertContentBlocks) ──");

test("UNPATCHED: undefined content crashes with 'some' error", () => {
  let error = null;
  try {
    convertContentBlocks_UNPATCHED(undefined);
  } catch (e) {
    error = e;
  }
  assert(error !== null, "Should have thrown an error");
  assert(error.message.includes("some"), `Error should mention 'some', got: ${error.message}`);
});

test("UNPATCHED: null content also crashes", () => {
  let error = null;
  try {
    convertContentBlocks_UNPATCHED(null);
  } catch (e) {
    error = e;
  }
  assert(error !== null, "Should have thrown an error");
});

test("PATCHED: undefined content returns fallback string", () => {
  const result = convertContentBlocks_PATCHED(undefined);
  assert(typeof result === "string", "Should return a string");
  assert(result.length > 0, "Should not be empty");
});

test("PATCHED: null content returns fallback string", () => {
  const result = convertContentBlocks_PATCHED(null);
  assert(typeof result === "string", "Should return a string");
});

test("PATCHED: valid content still works", () => {
  const result = convertContentBlocks_PATCHED([{ type: "text", text: "hello" }]);
  assert(result === "hello", `Expected 'hello', got '${result}'`);
});

// ============================================================================
// 2. kelly_calc execute must ALWAYS return valid AgentToolResult
// ============================================================================

/**
 * Safe wrapper that mirrors what the fixed trading-tools.ts should do.
 * Wraps the entire execute in try/catch to guarantee valid return.
 */
function kellyCalcSafe(_toolCallId, params) {
  try {
    const { prob_win, odds, fraction = 0.25, bankroll } = params || {};

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
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
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
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  } catch (e) {
    // Failsafe: if ANYTHING goes wrong, still return valid AgentToolResult
    return {
      content: [{ type: "text", text: `Error: ${e instanceof Error ? e.message : String(e)}` }],
    };
  }
}

console.log("\n── 2. kelly_calc safe wrapper ──");

test("valid params return content array", () => {
  const r = kellyCalcSafe("tc1", { prob_win: 0.6, odds: 2.0 });
  assert(Array.isArray(r.content), "content should be an array");
  assert(r.content.length === 1, "content should have one item");
  assert(r.content[0].type === "text", "content type should be 'text'");
});

test("undefined params don't crash", () => {
  const r = kellyCalcSafe("tc1", undefined);
  assert(Array.isArray(r.content), "content should be an array");
});

test("null params don't crash", () => {
  const r = kellyCalcSafe("tc1", null);
  assert(Array.isArray(r.content), "content should be an array");
});

test("empty object params return DO NOT BET", () => {
  const r = kellyCalcSafe("tc1", {});
  assert(Array.isArray(r.content), "content should be an array");
  const parsed = JSON.parse(r.content[0].text);
  assert(parsed.recommendation === "DO NOT BET", "should be DO NOT BET");
});

test("params from session log (prob_win=0.4, odds=2.5, bankroll=1079302)", () => {
  const r = kellyCalcSafe("tc1", { prob_win: 0.4, odds: 2.5, bankroll: 1079302, fraction: 0.25 });
  assert(Array.isArray(r.content), "content should be an array");
  const parsed = JSON.parse(r.content[0].text);
  assert(parsed.edge_exists === true, "should have edge");
  assert(typeof parsed.dollar_size === "number", "should have dollar_size");
});

test("content survives JSON.stringify/parse round-trip", () => {
  const r = kellyCalcSafe("tc1", { prob_win: 0.6, odds: 2.0 });
  const serialized = JSON.stringify(r);
  const deserialized = JSON.parse(serialized);
  assert(Array.isArray(deserialized.content), "content should survive round-trip");
  assert(deserialized.content[0].type === "text", "type should survive");
});

test("result with no undefined keys survives JSON round-trip", () => {
  const r = kellyCalcSafe("tc1", { prob_win: 0.6, odds: 2.0 });
  // Check that there are no undefined values that would be stripped by JSON
  const keys = Object.keys(r);
  for (const key of keys) {
    assert(r[key] !== undefined, `key '${key}' should not be undefined`);
  }
});

// ============================================================================
// 3. End-to-end: tool result → convertContentBlocks should not crash
// ============================================================================

console.log("\n── 3. End-to-end: toolResult → convertContentBlocks ──");

test("toolResult with kelly_calc content works in PATCHED convertContentBlocks", () => {
  const toolResult = kellyCalcSafe("tc1", { prob_win: 0.4, odds: 2.5, bankroll: 1079302 });
  const converted = convertContentBlocks_PATCHED(toolResult.content);
  assert(typeof converted === "string", "should produce a string");
  assert(converted.includes("full_kelly_pct"), "should contain kelly result");
});

test("toolResult with undefined content works in PATCHED convertContentBlocks", () => {
  // Simulate the broken scenario: toolResult with no content
  const brokenResult = { isError: false };
  const converted = convertContentBlocks_PATCHED(brokenResult.content);
  assert(typeof converted === "string", "should produce a fallback string");
});

test("round-tripped result works in PATCHED convertContentBlocks", () => {
  const r = kellyCalcSafe("tc1", { prob_win: 0.6, odds: 2.0 });
  // Simulate what happens when the message goes through session persistence
  const persisted = JSON.parse(JSON.stringify({ role: "toolResult", content: r.content }));
  const converted = convertContentBlocks_PATCHED(persisted.content);
  assert(typeof converted === "string", "should work after persistence round-trip");
});

// ============================================================================
// Summary
// ============================================================================
console.log(`\n── Results: ${passed} passed, ${failed} failed ──\n`);
process.exit(failed > 0 ? 1 : 0);
