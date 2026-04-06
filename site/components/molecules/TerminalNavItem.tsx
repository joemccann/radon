type TerminalNavItemProps = {
  label: string;
  active?: boolean;
  description?: string;
  onClick?: () => void;
  role?: string;
  ariaSelected?: boolean;
  id?: string;
};

export function TerminalNavItem({
  label,
  active = false,
  description,
  onClick,
  role,
  ariaSelected,
  id,
}: TerminalNavItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      role={role}
      aria-selected={ariaSelected}
      id={id}
      className={`group w-full text-left border-l-2 px-3 py-2 transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 ${
        active
          ? "border-accent bg-panel-raised/60 text-accent"
          : "border-transparent text-muted hover:border-grid hover:text-secondary"
      }`}
    >
      <div className="font-mono text-[11px] uppercase tracking-[0.16em]">{label}</div>
      {description ? (
        <p className="mt-2 text-[12px] leading-5 text-secondary">{description}</p>
      ) : null}
    </button>
  );
}
