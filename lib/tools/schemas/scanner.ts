import { Type, type Static } from "@sinclair/typebox";

// ── Input (maps to argparse) ──────────────────────────────────────────

export const ScannerInput = Type.Object({
  top: Type.Optional(Type.Integer({ minimum: 1, maximum: 500, description: "Number of top signals to show (default 20)" })),
  minScore: Type.Optional(Type.Number({ description: "Minimum score threshold (default 0)" })),
});

export type ScannerInput = Static<typeof ScannerInput>;

// ── Output (matches scanner.py JSON) ──────────────────────────────────

export const ScannerSignal = Type.Object({
  ticker: Type.String(),
  sector: Type.String(),
  score: Type.Number(),
  signal: Type.String(),
  direction: Type.String(),
  strength: Type.Number(),
  buy_ratio: Type.Union([Type.Number(), Type.Null()]),
  num_prints: Type.Number(),
  sustained_days: Type.Number(),
  recent_direction: Type.String(),
  recent_strength: Type.Number(),
});

export type ScannerSignal = Static<typeof ScannerSignal>;

export const ScannerOutput = Type.Object({
  scan_time: Type.String(),
  tickers_scanned: Type.Number(),
  signals_found: Type.Number(),
  top_signals: Type.Array(ScannerSignal),
});

export type ScannerOutput = Static<typeof ScannerOutput>;
