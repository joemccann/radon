/**
 * Demo signup marker decision.
 *
 * The SAME /sign-up page serves app.radon.run and demo.radon.run. Only the demo
 * deployment sets NEXT_PUBLIC_RADON_DEMO=1; there the sign-up page stamps the new
 * user's unsafeMetadata with `demo: true`, which the Clerk user.created webhook
 * reads (lib/demo/provisionTrial.ts:isDemoSignup) to provision a 3-day trial. On
 * prod the flag is unset, so no marker is set and app signups are unchanged.
 */
export function shouldMarkDemoSignup(
  flag: string | undefined = process.env.NEXT_PUBLIC_RADON_DEMO,
): boolean {
  return flag === "1";
}
