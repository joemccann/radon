"use client";

import { useEffect, useState } from "react";

const REDIRECT_AFTER_MS = 8_000;
const MARKETING_URL = "https://radon.run";

/**
 * Countdown + auto-forward to the marketing site. The visible link is the
 * primary affordance; the timer just saves a click for users who walk away.
 */
export default function TrialExpiredRedirect() {
  const [secondsLeft, setSecondsLeft] = useState(REDIRECT_AFTER_MS / 1000);

  useEffect(() => {
    const tick = setInterval(
      () => setSecondsLeft((s) => Math.max(0, s - 1)),
      1_000,
    );
    const jump = setTimeout(() => {
      window.location.replace(MARKETING_URL);
    }, REDIRECT_AFTER_MS);
    return () => {
      clearInterval(tick);
      clearTimeout(jump);
    };
  }, []);

  return (
    <div className="trial-expired-actions">
      <a className="trial-expired-cta" href={MARKETING_URL}>
        Continue to radon.run
      </a>
      <span className="trial-expired-countdown" aria-live="polite">
        Redirecting in {secondsLeft}s
      </span>
    </div>
  );
}
