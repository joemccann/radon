/**
 * Days-to-catalyst badge helper.
 *
 * Maps a `days_until` integer to a compact label + an urgency bucket so the
 * dashboard card and scanner rows render a consistent catalyst chip. The
 * urgency drives the brand token (imminent = warn, near = accent, distant =
 * muted); colour selection lives with the consumer so this stays pure.
 */

export type CatalystUrgency = "imminent" | "near" | "distant";

export interface CatalystBadge {
  label: string;
  urgency: CatalystUrgency;
}

const IMMINENT_MAX_DAYS = 1;
const NEAR_MAX_DAYS = 7;

function urgencyFor(daysUntil: number): CatalystUrgency {
  if (daysUntil <= IMMINENT_MAX_DAYS) return "imminent";
  if (daysUntil <= NEAR_MAX_DAYS) return "near";
  return "distant";
}

function labelFor(daysUntil: number): string {
  if (daysUntil <= 0) return "Today";
  return `${daysUntil}d`;
}

export function catalystBadge(daysUntil: number): CatalystBadge {
  return { label: labelFor(daysUntil), urgency: urgencyFor(daysUntil) };
}

export const CATALYST_URGENCY_TOKEN: Record<CatalystUrgency, string> = {
  imminent: "var(--negative)",
  near: "var(--signal-core)",
  distant: "var(--text-muted)",
};
