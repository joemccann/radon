// demo_users table accessor (demo.radon.run).
//
// One row per trial user, mirrored from Clerk publicMetadata at signup by the
// user.created webhook (Phase 2) and managed by the operator panel (Phase 6).
// The db client is INJECTED (the DEMO libsql client from getDemoDb, or a fake
// in tests) so this module never imports a live connection.
//
// Schema: scripts/db/demo_migrations/0001_demo_users.sql

export type DemoUserStatus = "active" | "expired" | "revoked";

export type DemoUserRow = {
  user_id: string;
  email: string | null;
  demo_role: string | null;
  started_at: string | null;
  expires_at: string | null;
  status: DemoUserStatus;
  revoked_at: string | null;
  created_at: string;
};

export type DemoDbClient = {
  execute: (args: {
    sql: string;
    args: unknown[];
  }) => Promise<{ rows: Array<Record<string, unknown>> }>;
};

export async function claimDemoWebhookEvent(
  db: DemoDbClient,
  eventId: string,
  eventType: string,
): Promise<boolean> {
  const result = await db.execute({
    sql: `INSERT INTO demo_webhook_events (event_id, event_type, processed_at)
          VALUES (?, ?, datetime('now'))
          ON CONFLICT(event_id) DO NOTHING
          RETURNING event_id`,
    args: [eventId, eventType],
  });
  return result.rows.length === 1;
}

export async function releaseDemoWebhookEvent(db: DemoDbClient, eventId: string): Promise<void> {
  await db.execute({
    sql: `DELETE FROM demo_webhook_events WHERE event_id = ?`,
    args: [eventId],
  });
}

function nowIso(now?: Date): string {
  return (now ?? new Date()).toISOString();
}

/**
 * Insert a trial row. A webhook retry may refresh identity fields, but it must
 * never reset the trial clock or reactivate an expired/revoked user.
 */
export async function upsertDemoUser(params: {
  db: DemoDbClient;
  userId: string;
  email: string | null;
  demoRole: string;
  startedAt: string;
  expiresAt: string;
  now?: Date;
}): Promise<void> {
  const { db, userId, email, demoRole, startedAt, expiresAt } = params;
  const created = nowIso(params.now);
  await db.execute({
    sql: `
      INSERT INTO demo_users
        (user_id, email, demo_role, started_at, expires_at, status, revoked_at, created_at)
      VALUES (?, ?, ?, ?, ?, 'active', NULL, ?)
      ON CONFLICT(user_id) DO UPDATE SET
        email      = excluded.email,
        demo_role  = excluded.demo_role
    `,
    args: [userId, email, demoRole, startedAt, expiresAt, created],
  });
}

function toRow(raw: Record<string, unknown>): DemoUserRow {
  const str = (v: unknown): string | null =>
    v === null || v === undefined ? null : String(v);
  return {
    user_id: String(raw.user_id),
    email: str(raw.email),
    demo_role: str(raw.demo_role),
    started_at: str(raw.started_at),
    expires_at: str(raw.expires_at),
    status: (str(raw.status) as DemoUserStatus) ?? "active",
    revoked_at: str(raw.revoked_at),
    created_at: String(raw.created_at),
  };
}

/** All trial rows, newest signup first. */
export async function listDemoUsers(db: DemoDbClient): Promise<DemoUserRow[]> {
  const res = await db.execute({
    sql: `SELECT user_id, email, demo_role, started_at, expires_at,
                 status, revoked_at, created_at
          FROM demo_users
          ORDER BY created_at DESC`,
    args: [],
  });
  return res.rows.map(toRow);
}

export async function getDemoUser(
  db: DemoDbClient,
  userId: string,
): Promise<DemoUserRow | null> {
  const res = await db.execute({
    sql: `SELECT user_id, email, demo_role, started_at, expires_at,
                 status, revoked_at, created_at
          FROM demo_users WHERE user_id = ?`,
    args: [userId],
  });
  const raw = res.rows[0];
  return raw ? toRow(raw) : null;
}

/** Operator REVOKE — kills the trial immediately. */
export async function revokeDemoUser(params: {
  db: DemoDbClient;
  userId: string;
  now?: Date;
}): Promise<void> {
  const { db, userId } = params;
  await db.execute({
    sql: `UPDATE demo_users
          SET status = 'revoked', revoked_at = ?
          WHERE user_id = ?`,
    args: [nowIso(params.now), userId],
  });
}

/** Operator EXTEND — push the expiry out and reactivate. */
export async function extendDemoUser(params: {
  db: DemoDbClient;
  userId: string;
  expiresAt: string;
}): Promise<void> {
  const { db, userId, expiresAt } = params;
  await db.execute({
    sql: `UPDATE demo_users
          SET expires_at = ?, status = 'active', revoked_at = NULL
          WHERE user_id = ?`,
    args: [expiresAt, userId],
  });
}

/** Per-endpoint AI burn for one user today (ET) — for the operator panel. */
export async function getDemoUserAiUsage(
  db: DemoDbClient,
  userId: string,
  dayEt: string,
): Promise<Record<string, number>> {
  const res = await db.execute({
    sql: `SELECT endpoint, call_count FROM demo_ai_usage
          WHERE user_id = ? AND day_et = ?`,
    args: [userId, dayEt],
  });
  const out: Record<string, number> = {};
  for (const row of res.rows) {
    const count = row.call_count;
    out[String(row.endpoint)] =
      typeof count === "number" ? count : Number(count ?? 0);
  }
  return out;
}
