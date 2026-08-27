import type { OrderSession } from "@/lib/orders/sessionWindow";

type SessionWindowChipProps = {
  session: OrderSession;
  testId?: string;
};

export default function SessionWindowChip({
  session,
  testId = "order-session-chip",
}: SessionWindowChipProps) {
  return (
    <span
      className={`order-session-chip order-session-chip--${session.tone}`}
      data-testid={testId}
      data-session={session.eligibility}
      title={session.hint}
    >
      {session.label}
    </span>
  );
}
