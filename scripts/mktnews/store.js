import { getDb, withDbBounds } from "../db/writer.js";
import { RING_SIZE } from "./normalize.js";

const UPSERT_SQL = `INSERT INTO headlines_ring (id, time, important, content, impact, received_at)
  VALUES (?, ?, ?, ?, ?, ?)
  ON CONFLICT(id) DO UPDATE SET
    time        = excluded.time,
    important   = excluded.important,
    content     = excluded.content,
    impact      = excluded.impact,
    received_at = excluded.received_at`;

const PRUNE_SQL = `DELETE FROM headlines_ring WHERE id NOT IN (
  SELECT id FROM headlines_ring ORDER BY received_at DESC, rowid DESC LIMIT ?)`;

const NEWEST_SQL = `SELECT id, time, important, content, impact
  FROM headlines_ring ORDER BY received_at DESC, rowid DESC LIMIT ?`;

function parseImpact(raw) {
  try {
    const parsed = JSON.parse(raw ?? "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function rowToHeadline(row) {
  return {
    kind: "headline",
    id: String(row.id),
    time: row.time == null ? null : String(row.time),
    important: Number(row.important) === 1,
    content: String(row.content),
    impact: parseImpact(row.impact),
  };
}

// Durable mirror of the hub ring: `load()` returns the newest `ringSize`
// headlines oldest-first (ring order); `put(item)` upserts one print and
// prunes the table back to `ringSize` in the same write batch.
export function createHeadlinesStore({
  db = null,
  ringSize = RING_SIZE,
  now = () => new Date().toISOString(),
} = {}) {
  const client = () => db ?? getDb();

  async function load() {
    const result = await withDbBounds("headlinesRing.load", () =>
      client().execute({ sql: NEWEST_SQL, args: [ringSize] }),
    );
    return result.rows.map(rowToHeadline).reverse();
  }

  async function put(item) {
    await withDbBounds("headlinesRing.put", () =>
      client().batch(
        [
          {
            sql: UPSERT_SQL,
            args: [
              item.id,
              item.time ?? null,
              item.important ? 1 : 0,
              item.content,
              JSON.stringify(item.impact ?? []),
              now(),
            ],
          },
          { sql: PRUNE_SQL, args: [ringSize] },
        ],
        "write",
      ),
    );
  }

  return { load, put };
}
