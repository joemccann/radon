import { Type, type Static } from "@sinclair/typebox";

// ── Input (maps to argparse) ──────────────────────────────────────────

export const KellyInput = Type.Object({
  prob: Type.Number({ minimum: 0, maximum: 1, description: "Probability of win (0-1)" }),
  odds: Type.Number({ exclusiveMinimum: 0, maximum: 1_000, description: "Win/loss odds ratio" }),
  fraction: Type.Optional(Type.Number({ exclusiveMinimum: 0, maximum: 0.5, description: "Kelly fraction (default 0.5 half Kelly; max 0.5; 0.25 optional stricter)" })),
  bankroll: Type.Optional(Type.Number({ minimum: 0, maximum: 1_000_000_000_000, description: "Current bankroll for dollar sizing" })),
  p_haircut: Type.Optional(Type.Number({ minimum: 0, maximum: 0.25, description: "Subtract from p before sizing (default 0.0)" })),
  p_source: Type.Optional(Type.Union([
    Type.Literal("estimated"),
    Type.Literal("measured"),
  ], { description: "estimated (default) may not exceed the configured fraction" })),
});

export type KellyInput = Static<typeof KellyInput>;

// ── Output (matches kelly.py JSON) ────────────────────────────────────

const nullableNumber = Type.Union([Type.Number(), Type.Null()]);

export const KellyOutput = Type.Object({
  full_kelly_pct: Type.Number(),
  fractional_kelly_pct: Type.Number(),
  fraction_used: Type.Number(),
  edge_exists: Type.Boolean(),
  recommendation: Type.String(),
  dollar_size: Type.Optional(Type.Number()),
  max_per_position: Type.Optional(Type.Number()),
  use_size: Type.Optional(Type.Number()),
  p_input: Type.Optional(Type.Number()),
  p_haircut: Type.Optional(Type.Number()),
  p_effective: Type.Optional(Type.Number()),
  p_source: Type.Optional(Type.String()),
  fraction_source: Type.Optional(Type.String()),
  growth_rate_full: Type.Optional(nullableNumber),
  growth_rate_used: Type.Optional(nullableNumber),
  restructure: Type.Optional(Type.Boolean()),
  capped: Type.Optional(Type.Boolean()),
});

export type KellyOutput = Static<typeof KellyOutput>;
