import { editorialNavLinks, DEMO_URL } from "@/lib/editorial-content";

export function EditorialFooter() {
  return (
    <footer className="border-t border-hairline-soft px-8 pb-14 pt-11 font-mono text-[11.5px] tracking-[0.03em] text-muted">
      <div className="mx-auto flex max-w-[1140px] flex-wrap items-baseline justify-between gap-6">
        <span>RADON · market-structure reconstruction · flow signal or nothing</span>
        <span className="flex flex-wrap gap-[22px]">
          {editorialNavLinks.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-secondary transition-colors hover:text-signal-deep"
            >
              {link.label}
            </a>
          ))}
          <a
            href={DEMO_URL}
            className="text-primary transition-colors hover:text-signal-deep"
          >
            Free demo
          </a>
        </span>
      </div>
    </footer>
  );
}
