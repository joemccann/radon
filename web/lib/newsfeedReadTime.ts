const WORDS_PER_MINUTE = 200;

function countWords(text: string): number {
  return text.split(/\s+/).filter((word) => word.length > 0).length;
}

/** Whole-minute reading estimate, never below one minute. */
export function estimateReadMinutes(text: string): number {
  return Math.max(1, Math.round(countWords(text) / WORDS_PER_MINUTE));
}
