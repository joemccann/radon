import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";

export default function (pi: ExtensionAPI) {
  // Kelly calculator as a native tool
  pi.registerTool({
    name: "kelly_calc",
    label: "Kelly Calculator",
    description: "Calculate fractional Kelly bet size given probability and odds",
    parameters: Type.Object({
      prob_win: Type.Number({ description: "Probability of winning (0-1)" }),
      odds: Type.Number({ description: "Win/loss ratio" }),
      fraction: Type.Optional(Type.Number({ description: "Kelly fraction, default 0.25" })),
      bankroll: Type.Optional(Type.Number({ description: "Current bankroll in dollars" })),
    }),
    async execute(_toolCallId: string, params: any) {
      try {
        const { prob_win, odds, fraction = 0.25, bankroll } = params ?? {};

        if (typeof prob_win !== "number" || !Number.isFinite(prob_win) ||
            typeof odds !== "number" || !Number.isFinite(odds) ||
            odds <= 0) {
          const result: Record<string, any> = {
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
            content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
          };
        }

        const q = 1 - prob_win;
        const fullKelly = prob_win - q / odds;
        const fracKelly = fullKelly * fraction;
        const result: Record<string, any> = {
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
          content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
        };
      } catch (e: any) {
        return {
          content: [{ type: "text" as const, text: `Error: ${e?.message ?? String(e)}` }],
        };
      }
    },
  });

  // Quick portfolio summary command
  pi.registerCommand("positions", {
    description: "Show current portfolio positions summary",
    handler: async (_args, ctx) => {
      ctx.sendUserMessage("/portfolio");
    },
  });
}
