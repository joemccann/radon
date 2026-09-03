export const ASSISTANT_TURN_FAILED_MESSAGE =
  "The assistant couldn't complete this turn. No order was placed. Try again or choose another model.";

const ASSISTANT_TIMEOUT_MESSAGE =
  "The turn timed out before the assistant answered. A smaller image or a shorter question may get through.";

/**
 * Convert transport status into operator-facing recovery copy.
 *
 * Provider messages are deliberately not accepted here. They can contain raw
 * JSON, request parameters, endpoint details, or other diagnostics that belong
 * in server logs rather than the transcript.
 */
export function assistantErrorMessage(status?: number): string {
  if (status === 408 || status === 504) return ASSISTANT_TIMEOUT_MESSAGE;
  if (status === 401) {
    return "Your assistant session expired. Refresh the page and try again.";
  }
  if (status === 403) {
    return "This account can't use the assistant. Contact an administrator.";
  }
  if (status === 413) {
    return "That message is too large. Remove an attachment or shorten it, then try again.";
  }
  if (status === 429) {
    return "The assistant has reached its usage limit. Try again later.";
  }
  return ASSISTANT_TURN_FAILED_MESSAGE;
}
