import { editorialNavLinks, DEMO_URL } from "@/lib/editorial-content";
import { GITHUB_URL, X_PROFILE_URL } from "@/lib/seo";

const footerLink =
  "inline-flex min-h-11 items-center text-secondary transition-colors hover:text-signal-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

export function EditorialFooter() {
  return (
    <footer className="border-t border-hairline-soft px-5 pb-14 pt-11 font-sans text-[12px] text-muted sm:px-8">
      <div className="mx-auto flex max-w-[1140px] flex-wrap items-baseline justify-between gap-6">
        <span>Radon · view, then the trade</span>
        <span className="flex flex-wrap gap-[22px]">
          {editorialNavLinks.map((link) => (
            <a key={link.href} href={link.href} className={footerLink}>
              {link.label}
            </a>
          ))}
          <a
            href={DEMO_URL}
            className={`${footerLink} text-primary`}
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
        <span aria-hidden="true">·</span>
        <a href="/privacy" className={footerLink}>
          Privacy
        </a>
        <span aria-hidden="true">·</span>
        <a href="/terms" className={footerLink}>
          Terms
        </a>
        <span aria-hidden="true">·</span>
        <a href="/developers" className={footerLink}>
          Developers
        </a>
      </div>
    </footer>
  );
}
