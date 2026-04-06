type CommandChipProps = {
  command: string;
  className?: string;
};

export function CommandChip({ command, className }: CommandChipProps) {
  return (
    <code
      className={[
        "inline-flex items-center rounded-[999px] border border-grid bg-canvas px-3 py-2 sm:py-1 font-mono text-[10px] uppercase tracking-[0.16em] text-secondary",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {command}
    </code>
  );
}
