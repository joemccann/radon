import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createClient } from "@libsql/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createHeadlinesStore } from "./store.js";

const MIGRATION = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../db/migrations/0062_headlines_ring.sql",
);

function statements(sql) {
  return sql
    .split("\n")
    .filter((line) => !line.trim().startsWith("--"))
    .join("\n")
    .split(/;\s*$/m)
    .map((s) => s.trim())
    .filter(Boolean);
}

function headline(id, extra = {}) {
  return {
    kind: "headline",
    id,
    time: `2026-08-30T03:00:0${id.slice(-1)}.000Z`,
    important: false,
    content: `print ${id}`,
    impact: [],
    ...extra,
  };
}

describe("createHeadlinesStore", () => {
  let db;
  let tick;
  let store;

  beforeEach(async () => {
    db = createClient({ url: ":memory:" });
    for (const stmt of statements(fs.readFileSync(MIGRATION, "utf8"))) await db.execute(stmt);
    tick = 0;
    store = createHeadlinesStore({
      db,
      ringSize: 3,
      now: () => `2026-08-30T04:00:${String(tick++).padStart(2, "0")}.000Z`,
    });
  });

  afterEach(() => db.close());

  it("loads the newest ringSize prints oldest-first and prunes the rest", async () => {
    for (const id of ["h1", "h2", "h3", "h4"]) await store.put(headline(id));
    expect((await store.load()).map((row) => row.id)).toEqual(["h2", "h3", "h4"]);
    const count = await db.execute("SELECT COUNT(*) AS n FROM headlines_ring");
    expect(Number(count.rows[0].n)).toBe(3);
  });

  it("re-putting an id moves it to the newest slot instead of duplicating", async () => {
    for (const id of ["h1", "h2", "h3"]) await store.put(headline(id));
    await store.put(headline("h1", { content: "print h1 (updated)" }));
    const rows = await store.load();
    expect(rows.map((row) => row.id)).toEqual(["h2", "h3", "h1"]);
    expect(rows[2].content).toBe("print h1 (updated)");
  });

  it("round-trips the headline shape the hub fans out", async () => {
    const item = headline("h7", {
      important: true,
      impact: [{ symbol: "WTI", impact: "bearish" }],
    });
    await store.put(item);
    expect(await store.load()).toEqual([item]);
  });

  it("starts empty", async () => {
    expect(await store.load()).toEqual([]);
  });
});
