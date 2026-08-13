import { runScript, type ScriptResult } from "../runner";
import { Type, type Static } from "@sinclair/typebox";

export const VCGInputSchema = Type.Object({
  proxy: Type.Optional(Type.Union([
    Type.Literal("HYG"),
    Type.Literal("JNK"),
    Type.Literal("LQD"),
  ])),
  backtest: Type.Optional(Type.Boolean()),
  days: Type.Optional(Type.Integer({ minimum: 1, maximum: 2_520 })),
});

export type VCGInput = Static<typeof VCGInputSchema>;

export interface VCGSignal {
  vcg: number | null;
  vcg_adj: number | null;      // was vcg_div — panic-adjusted z-score
  residual: number | null;
  beta1_vvix: number | null;
  beta2_vix: number | null;
  alpha: number | null;
  vix: number;
  vvix: number;
  credit_price: number;
  credit_5d_return_pct: number;
  ro: number;
  edr: number;                 // Early Divergence Risk (0|1)
  tier: 1 | 2 | 3 | null;     // severity tier when ro=1 or edr=1
  bounce: number;              // counter-signal bounce (0|1)
  vvix_severity: "extreme" | "elevated" | "moderate";
  sign_ok: boolean;
  sign_suppressed: boolean;
  pi_panic: number;
  regime: string;
  interpretation: "RISK_OFF" | "EDR" | "WATCH" | "BOUNCE" | "NORMAL" | "SUPPRESSED" | "PANIC" | string;
  attribution: {
    vvix_pct: number;
    vix_pct: number;
    vvix_component: number;
    vix_component: number;
    model_implied: number;
  };
}

export interface VCGOutput {
  scan_time: string;
  market_open: boolean;
  credit_proxy: string;
  signal: VCGSignal;
  history: Array<{
    date: string;
    residual: number | null;
    vcg: number | null;
    vcg_adj: number | null;    // was vcg_div
    beta1: number | null;
    beta2: number | null;
    vix: number;
    vvix: number;
    credit: number;
  }>;
}

export async function vcgScan(
  input: VCGInput = {},
): Promise<ScriptResult<VCGOutput>> {
  const args: string[] = ["--json"];

  if (input.proxy != null && !(["HYG", "JNK", "LQD"] as const).includes(input.proxy)) {
    throw new RangeError("proxy must be HYG, JNK, or LQD");
  }
  if (input.days != null && (!Number.isInteger(input.days) || input.days < 1 || input.days > 2_520)) {
    throw new RangeError("days must be an integer between 1 and 2520");
  }

  if (input.proxy) {
    args.push("--proxy", input.proxy);
  }
  if (input.backtest) {
    args.push("--backtest");
    if (input.days != null) {
      args.push("--days", String(input.days));
    }
  }

  return runScript("scripts/vcg_scan.py", {
    args,
    timeout: 60_000,
  }) as Promise<ScriptResult<VCGOutput>>;
}
