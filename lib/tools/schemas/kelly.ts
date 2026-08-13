import { Type, type Static } from "@sinclair/typebox";

// ── Input (maps to argparse) ──────────────────────────────────────────

export const KellyInput = Type.Object({
  prob: Type.Number({ minimum: 0, maximum: 1, description: "Probability of win (0-1)" }),
  odds: Type.Number({ exclusiveMinimum: 0, maximum: 1_000, description: "Win/loss odds ratio" }),
  fraction: Type.Optional(Type.Number({ exclusiveMinimum: 0, maximum: 1, description: "Kelly fraction (default 0.25)" })),
  bankroll: Type.Optional(Type.Number({ minimum: 0, maximum: 1_000_000_000_000, description: "Current bankroll for dollar sizing" })),
});

export type KellyInput = Static<typeof KellyInput>;

// ── Output (matches kelly.py JSON) ────────────────────────────────────

export const KellyOutput = Type.Object({
  full_kelly_pct: Type.Number(),
  fractional_kelly_pct: Type.Number(),
  fraction_used: Type.Number(),
  edge_exists: Type.Boolean(),
  recommendation: Type.String(),
  dollar_size: Type.Optional(Type.Number()),
  max_per_position: Type.Optional(Type.Number()),
  use_size: Type.Optional(Type.Number()),
});

export type KellyOutput = Static<typeof KellyOutput>;
