import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { resolve } from "path";

export const runtime = "nodejs";

const PROJECT_ROOT = resolve(process.cwd(), "..");
const CONFIG_PATH = resolve(PROJECT_ROOT, "data", "flex_token_config.json");

interface FlexTokenConfig {
  token_masked: string;
  activated_at: string;
  expires_at: string;
  renewal_url: string;
  breadcrumb: string;
  reminder_days: number[];
  reminders_sent: Record<string, string>;
  notes: string;
}

export async function GET(): Promise<Response> {
  try {
    const raw = await readFile(CONFIG_PATH, "utf-8");
    const config: FlexTokenConfig = JSON.parse(raw);

    const expiresAt = new Date(config.expires_at);
    const now = new Date();
    const diffMs = expiresAt.getTime() - now.getTime();
    const days_remaining = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    const thresholds = config.reminder_days ?? [30, 14, 7, 1];
    const should_warn = thresholds.some((t) => days_remaining <= t);
    const expired = days_remaining <= 0;

    // Which threshold are we at?
    let active_threshold: number | null = null;
    for (const t of [...thresholds].sort((a, b) => b - a)) {
      if (days_remaining <= t) {
        active_threshold = t;
        break;
      }
    }

    return NextResponse.json({
      days_remaining,
      expires_at: config.expires_at,
      activated_at: config.activated_at,
      token_masked: config.token_masked,
      renewal_url: config.renewal_url,
      breadcrumb: config.breadcrumb,
      should_warn,
      expired,
      active_threshold,
      reminder_days: thresholds,
    });
  } catch {
    // Config not found or invalid — not an error, just no data
    return NextResponse.json({
      days_remaining: null,
      should_warn: false,
      expired: false,
      error: "flex_token_config.json not found",
    });
  }
}
