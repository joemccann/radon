import { dbExecute } from "@/lib/dbExecute";

const MISSING_TABLE = /no such table:\s*newsfeed_explanations/i;

export type ExplainCacheRow = {
  explanation: string;
  model: string;
  provider: string;
  hasImage: boolean;
};

export type ExplainCacheWrite = {
  postId: string;
  contentHash: string;
  explanation: string;
  model: string;
  provider: string;
  hasImage: boolean;
};

function isMissingTable(error: unknown): boolean {
  return error instanceof Error && MISSING_TABLE.test(error.message);
}

export async function readExplainCache(
  postId: string,
  contentHash: string,
): Promise<ExplainCacheRow | null> {
  try {
    const result = await dbExecute(
      {
        sql: `SELECT explanation, model, provider, has_image
              FROM newsfeed_explanations
              WHERE post_id = ? AND content_hash = ?`,
        args: [postId, contentHash],
      },
      { label: "newsfeed-explain-read" },
    );
    const row = result.rows[0] as
      | { explanation?: unknown; model?: unknown; provider?: unknown; has_image?: unknown }
      | undefined;
    if (!row || typeof row.explanation !== "string" || !row.explanation) return null;
    return {
      explanation: row.explanation,
      model: typeof row.model === "string" ? row.model : "",
      provider: typeof row.provider === "string" ? row.provider : "",
      hasImage: Number(row.has_image) === 1,
    };
  } catch (error) {
    if (isMissingTable(error)) return null;
    throw error;
  }
}

export async function writeExplainCache(row: ExplainCacheWrite): Promise<void> {
  try {
    await dbExecute(
      {
        sql: `INSERT INTO newsfeed_explanations
                (post_id, content_hash, explanation, model, provider, has_image, created_at)
              VALUES (?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(post_id, content_hash) DO UPDATE SET
                explanation = excluded.explanation,
                model = excluded.model,
                provider = excluded.provider,
                has_image = excluded.has_image,
                created_at = excluded.created_at`,
        args: [
          row.postId,
          row.contentHash,
          row.explanation,
          row.model,
          row.provider,
          row.hasImage ? 1 : 0,
          new Date().toISOString(),
        ],
      },
      { label: "newsfeed-explain-write" },
    );
  } catch (error) {
    if (isMissingTable(error)) return;
    throw error;
  }
}
