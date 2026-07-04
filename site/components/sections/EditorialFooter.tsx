import { editorialNavLinks, DEMO_URL } from "@/lib/editorial-content";
import { GITHUB_URL, X_PROFILE_URL } from "@/lib/seo";

const footerLink = "text-secondary transition-colors hover:text-signal-deep";

export function EditorialFooter() {
  return (
    <footer className="border-t border-hairline-soft px-8 pb-14 pt-11 font-mono text-[11.5px] tracking-[0.03em] text-muted">
      <div className="mx-auto flex max-w-[1140px] flex-wrap items-baseline justify-between gap-6">
        <span>RADON · market-structure reconstruction · flow signal or nothing</span>
        <span className="flex flex-wrap gap-[22px]">
          {editorialNavLinks.map((link) => (
            <a key={link.href} href={link.href} className={footerLink}>
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
      <div className="mx-auto mt-5 flex max-w-[1140px] flex-wrap items-baseline gap-[10px]">
        <span>Built by Joe McCann</span>
        <span aria-hidden="true">·</span>
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={footerLink}
        >
          GitHub
        </a>
        <span aria-hidden="true">·</span>
        <a
          href={X_PROFILE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={footerLink}
        >
          @joemccann
        </a>
      </div>
    </footer>
  );
}
