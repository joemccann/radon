import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { ALLOWED_IMAGE_HOSTS, isAllowedImageUrl } from "@/lib/imageHosts";
import { requireRouteAccess } from "@/lib/routeAccess";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const USERNAME_PATTERN = /^[A-Za-z0-9_\- ]{1,32}$/;
const MAX_AVATAR_LENGTH = 256 * 1024; // ~256KB — keep Turso rows sane
const MAX_UI_PREFERENCES_LENGTH = 8 * 1024;
const UI_PREFERENCE_KEYS = new Set(["theme", "columns"]);

type ProfileRow = {
  username: string | null;
  avatar_url: string | null;
  ui_preferences: Record<string, unknown> | null;
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

function validateUiPreferences(
  raw: unknown,
): { value: string | null } | { error: string } {
  if (raw === undefined || raw === null) return { value: null };
  if (typeof raw !== "object" || Array.isArray(raw)) {
    return { error: "ui_preferences must be a JSON object" };
  }
  const entries = raw as Record<string, unknown>;
  for (const key of Object.keys(entries)) {
    if (!UI_PREFERENCE_KEYS.has(key)) {
      return { error: `ui_preferences.${key} is not a known preference` };
    }
  }
  if (
    "theme" in entries &&
    entries.theme !== "dark" &&
    entries.theme !== "light"
  ) {
    return { error: "ui_preferences.theme must be dark or light" };
  }
  const serialized = JSON.stringify(entries);
  if (serialized.length > MAX_UI_PREFERENCES_LENGTH) {
    return { error: "ui_preferences exceeds size limit" };
  }
  return { value: serialized };
}

function parseUiPreferences(raw: unknown): Record<string, unknown> | null {
  if (typeof raw !== "string" || !raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {}
  return null;
}

function mergeUiPreferencesJson(
  existing: Record<string, unknown> | null,
  incoming: Record<string, unknown>,
): Record<string, unknown> {
  const base = existing ? { ...existing } : {};
  if ("theme" in incoming) {
    base.theme = incoming.theme;
  }
  if (incoming.columns && typeof incoming.columns === "object" && !Array.isArray(incoming.columns)) {
    const mergedColumns: Record<string, unknown> = {
      ...(typeof base.columns === "object" && base.columns && !Array.isArray(base.columns)
        ? (base.columns as Record<string, unknown>)
        : {}),
    };
    for (const [tableId, table] of Object.entries(incoming.columns as Record<string, unknown>)) {
      if (!table || typeof table !== "object" || Array.isArray(table)) continue;
      const prior =
        mergedColumns[tableId] && typeof mergedColumns[tableId] === "object"
          ? (mergedColumns[tableId] as Record<string, unknown>)
          : {};
      mergedColumns[tableId] = { ...prior, ...(table as Record<string, unknown>) };
    }
    base.columns = mergedColumns;
  }
  return base;
}

async function readProfile(userId: string): Promise<ProfileRow> {
  const result = await dbExecute(
    {
      sql: `SELECT username, avatar_url, ui_preferences FROM user_profiles WHERE user_id = ? LIMIT 1`,
      args: [userId],
    },
    { label: "profile" },
  );
  if (result.rows.length === 0) {
    return { username: null, avatar_url: null, ui_preferences: null };
  }
  const row = result.rows[0] as unknown as {
    username: string | null;
    avatar_url: string | null;
    ui_preferences: string | null;
  };
  const stored = row.avatar_url ?? null;
  // Avatars stored before the host allowlist existed may point somewhere CSP
  // now blocks. Dropping them here degrades to the default avatar instead of a
  // broken image the user cannot explain or clear.
  const avatar_url = stored && isAllowedImageUrl(stored) ? stored : null;
  return {
    username: row.username ?? null,
    avatar_url,
    ui_preferences: parseUiPreferences(row.ui_preferences),
  };
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
  // mutate.workspace pin: chat call_api can reach this handler, so the
  // deployment gate (allowlist, demo state, per-user budget) runs here, not
  // only in the middleware perimeter.
  const access = await requireRouteAccess(req, {
    rate: { key: "profile:mutate", limit: 20, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const { userId } = access.principal;

  let body: { username?: unknown; avatar_url?: unknown; ui_preferences?: unknown };
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
  const hasUiPreferences = body.ui_preferences !== undefined;

  let nextUiPreferences: string | null = null;
  if (hasUiPreferences) {
    const uiResult = validateUiPreferences(body.ui_preferences);
    if ("error" in uiResult) {
      return setNoStoreResponseHeaders(
        jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: uiResult.error, requestId }),
        requestId,
      );
    }
    const existing = await readProfile(userId);
    const merged = mergeUiPreferencesJson(
      existing.ui_preferences,
      JSON.parse(uiResult.value ?? "{}") as Record<string, unknown>,
    );
    nextUiPreferences = JSON.stringify(merged);
  }

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
    if (hasUiPreferences) {
      await dbExecute({
        sql: `INSERT INTO user_profiles (user_id, username, avatar_url, ui_preferences, updated_at)
              VALUES (?, NULL, NULL, ?, datetime('now'))
              ON CONFLICT(user_id) DO UPDATE SET ui_preferences = excluded.ui_preferences,
                updated_at = excluded.updated_at`,
        args: [userId, nextUiPreferences],
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
