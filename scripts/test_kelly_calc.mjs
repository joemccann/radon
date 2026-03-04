#!/usr/bin/env node
/**
 * Red/Green tests for kelly_calc fixes:
 *   1. kelly_calc tool: odds <= 0 must not produce Infinity/NaN (division by zero guard)
 *   2. compaction.js estimateTokens: non-iterable message.content must not throw
 *
 * Run: node scripts/test_kelly_calc.mjs
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
// 1. kelly_calc tool logic (extracted from trading-tools.ts)
// ============================================================================

/** 
 * Mirrors the execute() logic in .pi/extensions/trading-tools.ts
 * Returns the inner result object for computation testing
 */
function kellyCalc({ prob_win, odds, fraction = 0.25, bankroll }) {
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
    return JSON.parse(JSON.stringify(result));
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
  return JSON.parse(JSON.stringify(result));
}

console.log("\n── kelly_calc tool (division by zero guard) ──");

test("odds=0 returns DO NOT BET without crash", () => {
  const r = kellyCalc({ prob_win: 0.5, odds: 0 });
  assert(r.recommendation === "DO NOT BET", `expected 'DO NOT BET', got '${r.recommendation}'`);
  assert(r.edge_exists === false, "edge_exists should be false");
  assert(r.full_kelly_pct === 0, "full_kelly_pct should be 0");
});

test("odds=-1 returns DO NOT BET without crash", () => {
  const r = kellyCalc({ prob_win: 0.5, odds: -1 });
  assert(r.recommendation === "DO NOT BET", `expected 'DO NOT BET', got '${r.recommendation}'`);
});

test("odds=0 with bankroll returns zero dollar_size", () => {
  const r = kellyCalc({ prob_win: 0.5, odds: 0, bankroll: 100000 });
  assert(r.dollar_size === 0, `expected dollar_size=0, got ${r.dollar_size}`);
  assert(r.max_per_position === 2500, `expected max_per_position=2500, got ${r.max_per_position}`);
});

test("odds=0 result has no Infinity or NaN values", () => {
  const r = kellyCalc({ prob_win: 0.5, odds: 0 });
  const json = JSON.stringify(r);
  assert(!json.includes("Infinity"), "result contains Infinity");
  assert(!json.includes("NaN"), "result contains NaN");
  assert(!json.includes("null"), "result contains null (from JSON.stringify of Infinity)");
});

test("valid input still works: 60% prob, 2:1 odds", () => {
  const r = kellyCalc({ prob_win: 0.6, odds: 2.0 });
  assert(r.edge_exists === true, "should have edge");
  assert(r.full_kelly_pct > 0, "full_kelly_pct should be positive");
  assert(r.recommendation !== "DO NOT BET", "should recommend a bet");
});

console.log("\n── kelly_calc tool (undefined/null param guard) ──");

test("undefined odds does not throw", () => {
  const r = kellyCalc({ prob_win: 0.5, odds: undefined });
  assert(r.recommendation === "DO NOT BET", `expected 'DO NOT BET', got '${r.recommendation}'`);
  assert(r.edge_exists === false, "edge_exists should be false");
});

test("null odds does not throw", () => {
  const r = kellyCalc({ prob_win: 0.5, odds: null });
  assert(r.recommendation === "DO NOT BET", `expected 'DO NOT BET', got '${r.recommendation}'`);
});

test("undefined prob_win does not throw", () => {
  const r = kellyCalc({ prob_win: undefined, odds: 2.0 });
  assert(r.recommendation === "DO NOT BET", `expected 'DO NOT BET', got '${r.recommendation}'`);
  assert(r.edge_exists === false, "edge_exists should be false");
});

test("null prob_win does not throw", () => {
  const r = kellyCalc({ prob_win: null, odds: 2.0 });
  assert(r.recommendation === "DO NOT BET", `expected 'DO NOT BET', got '${r.recommendation}'`);
});

test("empty object does not throw", () => {
  const r = kellyCalc({});
  assert(r.recommendation === "DO NOT BET", `expected 'DO NOT BET', got '${r.recommendation}'`);
});

test("undefined/null params result has no NaN or Infinity", () => {
  const r = kellyCalc({ prob_win: undefined, odds: undefined });
  const json = JSON.stringify(r);
  assert(!json.includes("NaN"), "result contains NaN");
  assert(!json.includes("Infinity"), "result contains Infinity");
  assert(!json.includes("null"), "result contains null from NaN serialization");
});

// ============================================================================
// 1b. kelly_calc tool AgentToolResult format (Pi extension API requirement)
// ============================================================================

/**
 * Mirrors the ACTUAL execute() from trading-tools.ts with AgentToolResult format
 * Pi requires: { content: [{ type: "text", text: "..." }], details: ... }
 */
function kellyCalcWithFormat(params) {
  try {
    const { prob_win, odds, fraction = 0.25, bankroll } = params ?? {};
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
    return {
      content: [{ type: "text", text: `Error: ${e.message}` }],
    };
  }
}

console.log("\n── kelly_calc tool (AgentToolResult format) ──");

test("returns object with content array", () => {
  const r = kellyCalcWithFormat({ prob_win: 0.6, odds: 2.0 });
  assert(typeof r === "object" && r !== null, "result should be an object");
  assert(Array.isArray(r.content), "result.content should be an array");
});

test("content has type 'text' with text property", () => {
  const r = kellyCalcWithFormat({ prob_win: 0.6, odds: 2.0 });
  assert(r.content.length === 1, "content should have one item");
  assert(r.content[0].type === "text", "content type should be 'text'");
  assert(typeof r.content[0].text === "string", "content text should be a string");
});

test("content.text is valid JSON with expected fields", () => {
  const r = kellyCalcWithFormat({ prob_win: 0.6, odds: 2.0 });
  const parsed = JSON.parse(r.content[0].text);
  assert("full_kelly_pct" in parsed, "should have full_kelly_pct");
  assert("recommendation" in parsed, "should have recommendation");
  assert("edge_exists" in parsed, "should have edge_exists");
});

test("invalid params still returns valid AgentToolResult format", () => {
  const r = kellyCalcWithFormat({ prob_win: undefined, odds: 0 });
  assert(typeof r === "object" && r !== null, "result should be an object");
  assert(Array.isArray(r.content), "result.content should be an array");
  assert(r.content[0].type === "text", "content type should be 'text'");
});

// ============================================================================
// 2. compaction.js estimateTokens (non-iterable content guard)
// ============================================================================

/** Mirrors the patched estimateTokens from compaction.js */
function estimateTokens(message) {
  let chars = 0;
  switch (message.role) {
    case "user": {
      const content = message.content;
      if (typeof content === "string") {
        chars = content.length;
      } else if (Array.isArray(content)) {
        for (const block of content) {
          if (block.type === "text" && block.text) {
            chars += block.text.length;
          }
        }
      }
      return Math.ceil(chars / 4);
    }
    case "assistant": {
      // PATCHED: guard against non-iterable content
      if (!Array.isArray(message.content)) return 0;
      for (const block of message.content) {
        if (block.type === "text") {
          chars += block.text.length;
        } else if (block.type === "thinking") {
          chars += block.thinking.length;
        } else if (block.type === "toolCall") {
          chars += block.name.length + JSON.stringify(block.arguments).length;
        }
      }
      return Math.ceil(chars / 4);
    }
    case "custom":
    case "toolResult": {
      if (typeof message.content === "string") {
        chars = message.content.length;
      }
      // PATCHED: else if Array.isArray instead of bare else
      else if (Array.isArray(message.content)) {
        for (const block of message.content) {
          if (block.type === "text" && block.text) {
            chars += block.text.length;
          }
          if (block.type === "image") {
            chars += 4800;
          }
        }
      }
      return Math.ceil(chars / 4);
    }
  }
  return 0;
}

console.log("\n── estimateTokens (non-iterable content guard) ──");

test("toolResult with null content does not throw", () => {
  const tokens = estimateTokens({ role: "toolResult", content: null });
  assert(tokens === 0, `expected 0, got ${tokens}`);
});

test("toolResult with undefined content does not throw", () => {
  const tokens = estimateTokens({ role: "toolResult", content: undefined });
  assert(tokens === 0, `expected 0, got ${tokens}`);
});

test("toolResult with numeric content does not throw", () => {
  const tokens = estimateTokens({ role: "toolResult", content: 42 });
  assert(tokens === 0, `expected 0, got ${tokens}`);
});

test("assistant with null content does not throw", () => {
  const tokens = estimateTokens({ role: "assistant", content: null });
  assert(tokens === 0, `expected 0, got ${tokens}`);
});

test("assistant with undefined content does not throw", () => {
  const tokens = estimateTokens({ role: "assistant", content: undefined });
  assert(tokens === 0, `expected 0, got ${tokens}`);
});

test("toolResult with string content still works", () => {
  const tokens = estimateTokens({ role: "toolResult", content: "hello world" });
  assert(tokens === Math.ceil(11 / 4), `expected ${Math.ceil(11 / 4)}, got ${tokens}`);
});

test("toolResult with array content still works", () => {
  const tokens = estimateTokens({
    role: "toolResult",
    content: [{ type: "text", text: "hello world" }],
  });
  assert(tokens === Math.ceil(11 / 4), `expected ${Math.ceil(11 / 4)}, got ${tokens}`);
});

test("assistant with array content still works", () => {
  const tokens = estimateTokens({
    role: "assistant",
    content: [{ type: "text", text: "response text" }],
  });
  assert(tokens === Math.ceil(13 / 4), `expected ${Math.ceil(13 / 4)}, got ${tokens}`);
});

// ============================================================================
// Summary
// ============================================================================
console.log(`\n── Results: ${passed} passed, ${failed} failed ──\n`);
process.exit(failed > 0 ? 1 : 0);
