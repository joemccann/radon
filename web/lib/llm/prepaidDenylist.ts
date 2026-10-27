/**
 * Prepaid console keys the web LLM layer never reads or sends.
 *
 * Operator policy 2026-10-26 (absolute): Anthropic, OpenAI/Codex, xAI/Grok and
 * Antigravity run only on consumer subscriptions. This is the one web file
 * allowed to name these variables; it only detects that one is set, never
 * reads its value. Same list as scripts/clients/prepaid_denylist.json
 * (parity: scripts/tests/test_subscriptions_only_policy.py).
 */

export const IGNORED_PREPAID_ENV = [
  "ANTHROPIC_API_KEY",
  "CLAUDE_CODE_API_KEY",
  "CLAUDE_API_KEY",
  "XAI_API_KEY",
  "GROK_API_KEY",
  "OPENAI_API_KEY",
  "RADON_LADDER_ALLOW_PREPAID",
] as const;

const warned = new Set<string>();

/** One warning per set deny-listed variable per process, naming it, never its value. */
export function warnIgnoredPrepaidEnv(): void {
  for (const name of IGNORED_PREPAID_ENV) {
    if (warned.has(name) || !process.env[name]?.trim()) continue;
    warned.add(name);
    console.warn(`[radon] ignoring ${name}: prepaid keys are never used (subscriptions only)`);
  }
}

/** Test seam: forget which variables were already reported. */
export function resetPrepaidWarnings(): void {
  warned.clear();
}
