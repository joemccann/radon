/** Reject sustained adjacent phrase loops before content reaches the client. */
export function hasRepetitiveText(text: string): boolean {
  const words = text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  for (let period = 1; period <= Math.min(64, Math.floor(words.length / 4)); period += 1) {
    let matched = 0;
    for (let i = period; i < words.length; i += 1) {
      matched = words[i] === words[i - period] ? matched + 1 : 0;
      // Require four full copies and at least 24 words, avoiding ordinary
      // repeated headings or a brief quotation.
      if (matched >= Math.max(3 * period, 24 - period)) return true;
    }
  }
  return false;
}

export class AssistantResponseQualityError extends Error {
  constructor() {
    super("Assistant returned an invalid completion.");
    this.name = "AssistantResponseQualityError";
  }
}
