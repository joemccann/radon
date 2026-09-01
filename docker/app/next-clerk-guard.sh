#!/bin/sh
set -eu

# Fail closed before `next start`. NEXT_PUBLIC_* is compile-time inlined;
# an empty bake ships /sign-in as Missing publishableKey and --env-file
# cannot repair the client bundle. Refuse to boot unless the key is in
# env AND in .next/static.

key="${NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:-}"
static="${NEXT_CLERK_STATIC_DIR:-/home/radon/radon/web/.next/static}"

case "$key" in
  pk_live_*|pk_test_*) ;;
  *)
    echo "next-clerk-guard: missing NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" >&2
    exit 78
    ;;
esac

if [ "${#key}" -lt 20 ]; then
  echo "next-clerk-guard: publishableKey too short" >&2
  exit 78
fi

if [ ! -d "$static" ]; then
  echo "next-clerk-guard: client bundle directory missing: $static" >&2
  exit 78
fi

if ! grep -RF -- "$key" "$static" >/dev/null 2>&1; then
  echo "next-clerk-guard: publishableKey not in client bundle; image was baked empty" >&2
  exit 78
fi

if [ "${NEXT_CLERK_GUARD_TEST:-0}" = "1" ]; then
  exit 0
fi

cd /home/radon/radon/web
exec bun run start
