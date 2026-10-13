import { routeMetadata } from "@/lib/pageTitle";

// Public landing page for expired demo trials (middleware isPublicRoute).
// The demo gate redirects signed-in expired trial users here; it renders
// outside WorkspaceShell, same bare pattern as /sign-in.
export const metadata = routeMetadata("/trial-expired");

export default function TrialExpiredPage() {
  return (
    <div className="trial-expired-page">
      <div className="trial-expired-panel" role="status">
        <div className="trial-expired-kicker">
          <span className="trial-expired-dot" aria-hidden />
          Radon Terminal
        </div>
        <h1 className="trial-expired-title">Your demo has ended</h1>
        <p className="trial-expired-copy">
          Thanks for exploring Radon. Your trial window of 2 trading days has
          expired, so sample market access is now closed for this account.
        </p>
        <p className="trial-expired-copy">
          To learn more about Radon, or to get in touch about full access,
          head to the main site.
        </p>
        {/* No auto-forward: a timed redirect swallowed this state and left
            expired users on the marketing hero with no word that the trial
            ended. The link is the only exit. */}
        <div className="trial-expired-actions">
          <a className="trial-expired-cta" href="https://radon.run">
            Continue to radon.run
          </a>
        </div>
      </div>
    </div>
  );
}
