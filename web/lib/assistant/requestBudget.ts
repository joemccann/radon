import type { ApiMessage } from "../types";

/** R-314 / REL-317: UTF-8 text bounds also bound tokenizer input before I/O. */
export const MAX_ASSISTANT_MESSAGE_TEXT_BYTES = 32_000;
export const MAX_ASSISTANT_TURN_TEXT_BYTES = 128_000;
const MAX_MESSAGES = 40;

function messageTextBytes(message: unknown): number {
  const content = (message as { content?: unknown } | null)?.content;
  if (typeof content === "string") return new TextEncoder().encode(content).length;
  if (!Array.isArray(content)) return 0;
  return content.reduce((bytes, block) => {
    if (block?.type !== "text" || typeof block.text !== "string") return bytes;
    return bytes + new TextEncoder().encode(block.text).length;
  }, 0);
}

export function textPayloadViolation(messages: unknown[]): { status: 413; error: string } | null {
  let total = 0;
  for (const message of messages) {
    const bytes = messageTextBytes(message);
    if (bytes > MAX_ASSISTANT_MESSAGE_TEXT_BYTES) {
      return { status: 413, error: "Message text too large. Shorten it and try again." };
    }
    total += bytes;
    if (total > MAX_ASSISTANT_TURN_TEXT_BYTES) {
      return { status: 413, error: "Conversation text too large. Start a shorter conversation." };
    }
  }
  return null;
}

/** Keep the newest contiguous usable context; never truncate the current prompt. */
export function boundedAssistantHistory(history: ApiMessage[], current: ApiMessage): ApiMessage[] {
  const recent: ApiMessage[] = [];
  let total = messageTextBytes(current);
  for (let i = history.length - 1; i >= 0 && recent.length < MAX_MESSAGES - 1; i--) {
    const bytes = messageTextBytes(history[i]);
    if (bytes > MAX_ASSISTANT_MESSAGE_TEXT_BYTES || total + bytes > MAX_ASSISTANT_TURN_TEXT_BYTES) break;
    recent.push(history[i]);
    total += bytes;
  }
  return [...recent.reverse(), current];
}
