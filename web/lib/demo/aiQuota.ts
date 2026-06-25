// Per-trial AI quota guard (demo.radon.run).
//
// `checkAndIncrementQuota` atomically inserts/increments the demo_ai_usage row
// for today (ET) in the DEMO libsql DB and reports whether the call is allowed.
// The db client is INJECTED (not imported) so route handlers pass the demo DB
// and tests pass a fake.
//
// Edge-safe-ish: ET day via Intl (no node:*). Default limits per the plan.

export type QuotaDbClient = {
  execute: (args: {
    sql: string;
    args: unknown[];
  }) => Promise<{ rows: Array<Record<string, unknown>> }>;
};

export type QuotaResult = {
  allowed: boolean;
  count: number;
  limit: number;
  resetAtEt: string;
};

// Per-endpoint daily limits (plan §Guardrails — AI quotas).
export const DEMO_AI_DAILY_LIMITS: Record<string, number> = {
  assistant: 5,
  "ticker/seasonality": 10,
  "ticker/info": 20,
};

const _ET_DATE_FMT = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** YYYY-MM-DD for the given instant in America/New_York. */
export function etDayKey(now: Date = new Date()): string {
  // en-CA formats as YYYY-MM-DD.
  return _ET_DATE_FMT.format(now);
}

/** ISO-ET midnight of the NEXT ET day — when the quota resets. */
export function etResetAt(now: Date = new Date()): string {
  const today = etDayKey(now);
  const [y, m, d] = today.split("-").map((n) => Number.parseInt(n, 10));
  // 00:00 ET tomorrow. ET is UTC-4/-5; we render a date-only + label rather
  // than chase the offset — consumers display "resets 00:00 ET <date>".
  const next = new Date(Date.UTC(y, m - 1, d + 1));
  const nextKey = `${next.getUTCFullYear()}-${String(next.getUTCMonth() + 1).padStart(2, "0")}-${String(next.getUTCDate()).padStart(2, "0")}`;
  return `${nextKey}T00:00:00-04:00`;
}

/**
 * Atomically record one AI call and report whether it is allowed.
 *
 * The increment is unconditional (an UPSERT) so concurrent calls can't race
 * past the limit; the returned `count` is the post-increment value and
 * `allowed` is `count <= limit`. A blocked call still increments — the limit
 * is a hard daily ceiling, not a leaky bucket — but the caller should 429 and
 * not perform the expensive work.
 */
export async function checkAndIncrementQuota(params: {
  userId: string;
  endpoint: string;
  db: QuotaDbClient;
  limit?: number;
  now?: Date;
}): Promise<QuotaResult> {
  const { userId, endpoint, db } = params;
  const now = params.now ?? new Date();
  const limit =
    params.limit ?? DEMO_AI_DAILY_LIMITS[endpoint] ?? 0;
  const day = etDayKey(now);

  await db.execute({
    sql: `
      INSERT INTO demo_ai_usage (user_id, endpoint, day_et, call_count)
      VALUES (?, ?, ?, 1)
      ON CONFLICT(user_id, endpoint, day_et)
      DO UPDATE SET call_count = call_count + 1
    `,
    args: [userId, endpoint, day],
  });

  const read = await db.execute({
    sql: `SELECT call_count FROM demo_ai_usage
          WHERE user_id = ? AND endpoint = ? AND day_et = ?`,
    args: [userId, endpoint, day],
  });

  const raw = read.rows[0]?.call_count;
  const count = typeof raw === "number" ? raw : Number(raw ?? 0);

  return {
    allowed: count <= limit,
    count,
    limit,
    resetAtEt: etResetAt(now),
  };
}
