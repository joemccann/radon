export const TICKER_PATTERN = /^[A-Z][A-Z0-9.-]{0,9}$/;
export const OPTION_EXPIRY_PATTERN = /^(?:\d{8}|\d{4}-\d{2}-\d{2})$/;

export function boundedTicker(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const symbol = value.trim().toUpperCase();
  return TICKER_PATTERN.test(symbol) ? symbol : null;
}

export function boundedPositiveInt(value: string | null, fallback: number, max: number): number | null {
  if (value === null || value === "") return fallback;
  if (!/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= max ? parsed : null;
}

export function boundedUniqueTickers(value: unknown, max: number): string[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > max) return null;
  const unique = new Set<string>();
  for (const item of value) {
    const symbol = boundedTicker(item);
    if (!symbol) return null;
    unique.add(symbol);
  }
  return [...unique];
}
