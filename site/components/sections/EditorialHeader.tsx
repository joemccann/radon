import { ThemeToggle } from "@/components/atoms/ThemeToggle";
import { editorialNavLinks, DEMO_URL } from "@/lib/editorial-content";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

export function EditorialHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-hairline-soft bg-canvas/[0.86] backdrop-blur-[10px] backdrop-saturate-150">
      <div className="mx-auto flex h-[58px] w-full max-w-[1140px] items-center justify-between px-8">
        <a href="#top" className={`flex items-center gap-[9px] ${focusRing}`}>
          <span
            aria-hidden="true"
            className="relative inline-block h-[13px] w-[13px] rounded-full border-[1.5px] border-accent after:absolute after:inset-[3px] after:rounded-full after:bg-accent after:content-['']"
          />
          <span className="font-mono text-[15px] font-semibold tracking-[0.04em] text-primary">
            RADON
          </span>
          <small className="font-mono text-[10.5px] uppercase tracking-[0.1em] text-muted">
            research instrument
          </small>
        </a>
        <nav aria-label="Primary" className="flex items-center gap-[26px]">
          {editorialNavLinks.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className={`hidden font-mono text-[12px] uppercase tracking-[0.04em] text-secondary transition-colors hover:text-signal-deep md:inline ${focusRing}`}
            >
              {link.label}
            </a>
          ))}
          <a
            href={DEMO_URL}
            className={`inline-block rounded-[4px] border border-grid px-[13px] py-[7px] font-mono text-[11px] uppercase tracking-[0.06em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep ${focusRing}`}
          >
            Try free demo
          </a>
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
