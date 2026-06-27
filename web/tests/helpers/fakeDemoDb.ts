// In-memory fake of the demo Turso DB modelling demo_users + demo_ai_usage,
// matching the SQL in lib/demo/demoUsers.ts. Shared by the demo DB-backed
// tests so each one doesn't re-implement the storage.

import type { DemoDbClient, DemoUserRow } from "@/lib/demo/demoUsers";

export type FakeDemoDb = DemoDbClient & {
  users: Map<string, DemoUserRow>;
  usage: Map<string, number>; // `${userId} ${endpoint} ${day}` → count
  setUsage: (userId: string, endpoint: string, day: string, count: number) => void;
};

export function makeFakeDemoDb(): FakeDemoDb {
  const users = new Map<string, DemoUserRow>();
  const usage = new Map<string, number>();
  const usageKey = (u: string, e: string, d: string) => `${u} ${e} ${d}`;

  const db: FakeDemoDb = {
    users,
    usage,
    setUsage(userId, endpoint, day, count) {
      usage.set(usageKey(userId, endpoint, day), count);
    },
    async execute({ sql, args }) {
      const a = args as unknown[];

      if (sql.includes("INSERT INTO demo_users")) {
        const [user_id, email, demo_role, started_at, expires_at, created_at] = a as string[];
        const existing = users.get(user_id);
        users.set(user_id, {
          user_id,
          email: email ?? null,
          demo_role: demo_role ?? null,
          started_at: started_at ?? null,
          expires_at: expires_at ?? null,
          status: "active",
          revoked_at: null,
          created_at: existing?.created_at ?? created_at,
        });
        return { rows: [] };
      }

      if (sql.includes("UPDATE demo_users") && sql.includes("status = 'revoked'")) {
        const [revoked_at, user_id] = a as string[];
        const row = users.get(user_id);
        if (row) users.set(user_id, { ...row, status: "revoked", revoked_at });
        return { rows: [] };
      }

      if (sql.includes("UPDATE demo_users") && sql.includes("status = 'active'")) {
        const [expires_at, user_id] = a as string[];
        const row = users.get(user_id);
        if (row) users.set(user_id, { ...row, expires_at, status: "active", revoked_at: null });
        return { rows: [] };
      }

      if (sql.includes("FROM demo_users") && sql.includes("WHERE user_id = ?")) {
        const [user_id] = a as string[];
        const row = users.get(user_id);
        return { rows: row ? [row as unknown as Record<string, unknown>] : [] };
      }

      if (sql.includes("FROM demo_users")) {
        // list — newest created_at first
        const rows = [...users.values()].sort((x, y) =>
          (y.created_at ?? "").localeCompare(x.created_at ?? ""),
        );
        return { rows: rows as unknown as Record<string, unknown>[] };
      }

      if (sql.includes("FROM demo_ai_usage")) {
        const [user_id, day] = a as string[];
        const out: Record<string, unknown>[] = [];
        for (const [k, count] of usage) {
          const [u, e, d] = k.split(" ");
          if (u === user_id && d === day) out.push({ endpoint: e, call_count: count });
        }
        return { rows: out };
      }

      return { rows: [] };
    },
  };
  return db;
}
