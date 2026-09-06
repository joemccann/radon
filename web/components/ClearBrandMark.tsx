/** The open R mark from the approved Clear direction; no bitmap request. */
export default function ClearBrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" fill="none" aria-hidden="true" focusable="false" style={{ color: "var(--signal-core-text)" }}>
      <path d="M4 22V6h9a6 6 0 0 1 0 12H9M15 18l7 4" stroke="currentColor" strokeWidth="3.3" />
      <circle cx="22" cy="6" r="2.3" fill="currentColor" />
    </svg>
  );
}
