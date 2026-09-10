import DemoPendingLogo from "./DemoPendingLogo";
import DemoPendingRetry from "./DemoPendingRetry";

// Public holding page for a demo signup whose trial has not landed yet
// (middleware isPublicRoute). The demo gate redirects here instead of the bare
// 403 that 4eaaf5e9 shipped. Deliberately a bare leaf: no workspace shell, no
// account data, no Clerk identity echoed — it is reachable unauthenticated on
// BOTH deployments, so it must render nothing a stranger should not see.
export const metadata = {
  title: "Setting up your demo · Radon",
};

export default function DemoPendingPage() {
  return (
    <div className="trial-expired-page">
      <div className="trial-expired-panel" role="status">
        <DemoPendingLogo />
        <div className="trial-expired-kicker">
          <span className="trial-expired-dot" aria-hidden />
          Radon Terminal
        </div>
        <h1 className="trial-expired-title">Setting up your demo</h1>
        <p className="trial-expired-copy">
          Your account is being provisioned. This usually takes a few seconds.
        </p>
        <DemoPendingRetry />
      </div>
    </div>
  );
}
