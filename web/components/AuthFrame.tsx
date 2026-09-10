import type { ReactNode } from "react";
import Link from "next/link";
import ClearBrandMark from "./ClearBrandMark";

/** Shared public framing only. Clerk continues to own identity and all forms. */
export default function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <main className="clear-auth">
      <Link className="clear-auth__brand" href="/" aria-label="Radon home"><ClearBrandMark />radon</Link>
      <div className="clear-auth__layout">
        <section className="clear-auth__intro" aria-labelledby="auth-workspace-title">
          <span className="clear-auth__eyebrow">Your trading workspace</span>
          <h1 id="auth-workspace-title">A clear view.<br />An informed decision.</h1>
          <p>Your portfolio, market research and risk. Together in one workspace.</p>
          <div className="clear-auth__principles">
            <span>See your exposure</span>
            <span>Follow the evidence</span>
            <span>Review before you trade</span>
          </div>
        </section>
        <div className="clear-auth__form">{children}</div>
      </div>
    </main>
  );
}
