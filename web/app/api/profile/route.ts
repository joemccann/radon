import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { ALLOWED_IMAGE_HOSTS, isAllowedImageUrl } from "@/lib/imageHosts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const USERNAME_PATTERN = /^[A-Za-z0-9_\- ]{1,32}$/;
const MAX_AVATAR_LENGTH = 256 * 1024; // ~256KB — keep Turso rows sane

type ProfileRow = {
  username: string | null;
  avatar_url: string | null;
};

function validateUsername(raw: unknown): { value: string } | { error: string } {
  if (raw === undefined || raw === null) return { value: "" };
  if (typeof raw !== "string") return { error: "username must be a string" };
  const trimmed = raw.trim();
  if (trimmed.length === 0) return { value: "" };
  if (!USERNAME_PATTERN.test(trimmed)) {
    return { error: "username must be 1-32 chars: letters, numbers, _, -, space" };
  }
  return { value: trimmed };
}

function validateAvatarUrl(raw: unknown): { value: string | null } | { error: string } {
  if (raw === undefined || raw === null) return { value: null };
  if (typeof raw !== "string") return { error: "avatar_url must be a string" };
  const trimmed = raw.trim();
  if (trimmed.length === 0) return { value: null };
  if (trimmed.length > MAX_AVATAR_LENGTH) return { error: "avatar_url exceeds size limit" };
  // Must match CSP img-src (lib/imageHosts.ts). Accepting a host the browser
  // will refuse to load stores an avatar that renders as a broken image, and
  // widening img-src instead would restore the wildcard that let an injected
  // markdown image in an assistant answer beacon account figures out.
  if (!isAllowedImageUrl(trimmed)) {
    return {
      error: `avatar_url must be a data:image URL or an https URL on ${ALLOWED_IMAGE_HOSTS.join(", ")}`,
    };
  }
  return { value: trimmed };
}

async function readProfile(userId: string): Promise<ProfileRow> {
  const result = await dbExecute(
    {
      sql: `SELECT username, avatar_url FROM user_profiles WHERE user_id = ? LIMIT 1`,
      args: [userId],
    },
    { label: "profile" },
  );
  if (result.rows.length === 0) return { username: null, avatar_url: null };
  const row = result.rows[0] as unknown as ProfileRow;
  const stored = row.avatar_url ?? null;
  // Avatars stored before the host allowlist existed may point somewhere CSP
  // now blocks. Dropping them here degrades to the default avatar instead of a
  // broken image the user cannot explain or clear.
  const avatar_url = stored && isAllowedImageUrl(stored) ? stored : null;
  return { username: row.username ?? null, avatar_url };
}

export const radonCapability = { GET: "read", PUT: "mutate.workspace" };

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
      requestId,
    );
  }
  try {
    const profile = await readProfile(userId);
    return setNoStoreResponseHeaders(NextResponse.json(profile), requestId);
  } catch (err) {
    // 503, not 500: transient DB outage — useProfile keeps its client-side
    // fallback on any !res.ok instead of freezing a blank profile.
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Profile store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}

export async function PUT(req: Request): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
      requestId,
    );
  }

  let body: { username?: unknown; avatar_url?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "BAD_REQUEST", message: "Invalid JSON body", requestId }),
      requestId,
    );
  }

  // PATCH semantics: only fields explicitly present in the body are changed.
  // An absent field is preserved (a username-only save must not wipe the
  // avatar, and vice versa); an explicit empty string clears the field.
  const hasUsername = body.username !== undefined;
  const hasAvatar = body.avatar_url !== undefined;

  let nextUsername: string | null = null;
  if (hasUsername) {
    const usernameResult = validateUsername(body.username);
    if ("error" in usernameResult) {
      return setNoStoreResponseHeaders(
        jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: usernameResult.error, requestId }),
        requestId,
      );
    }
    nextUsername = usernameResult.value.length > 0 ? usernameResult.value : null;
  }

  let nextAvatar: string | null = null;
  if (hasAvatar) {
    const avatarResult = validateAvatarUrl(body.avatar_url);
    if ("error" in avatarResult) {
      return setNoStoreResponseHeaders(
        jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: avatarResult.error, requestId }),
        requestId,
      );
    }
    nextAvatar = avatarResult.value;
  }

  try {
    if (hasUsername && hasAvatar) {
      await dbExecute({
        sql: `INSERT INTO user_profiles (user_id, username, avatar_url, updated_at)
              VALUES (?, ?, ?, datetime('now'))
              ON CONFLICT(user_id) DO UPDATE SET username = excluded.username,
                avatar_url = excluded.avatar_url, updated_at = excluded.updated_at`,
        args: [userId, nextUsername, nextAvatar],
      }, { label: "profile" });
    } else if (hasUsername) {
      await dbExecute({
        sql: `INSERT INTO user_profiles (user_id, username, avatar_url, updated_at)
              VALUES (?, ?, NULL, datetime('now'))
              ON CONFLICT(user_id) DO UPDATE SET username = excluded.username,
                updated_at = excluded.updated_at`,
        args: [userId, nextUsername],
      }, { label: "profile" });
    } else if (hasAvatar) {
      await dbExecute({
        sql: `INSERT INTO user_profiles (user_id, username, avatar_url, updated_at)
              VALUES (?, NULL, ?, datetime('now'))
              ON CONFLICT(user_id) DO UPDATE SET avatar_url = excluded.avatar_url,
                updated_at = excluded.updated_at`,
        args: [userId, nextAvatar],
      }, { label: "profile" });
    }
    const saved = await readProfile(userId);
    return setNoStoreResponseHeaders(NextResponse.json(saved), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Profile store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}
