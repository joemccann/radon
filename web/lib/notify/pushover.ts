// Minimal Pushover sender for operator alerts raised from Next.js.
//
// No-ops when unconfigured, and never throws: an alert must not be able to fail
// the request that raised it. Messages carry counts and reasons only — never a
// user id, email, or any other end-user identifier.

const PUSHOVER_URL = "https://api.pushover.net/1/messages.json";

export async function sendPushover(params: {
  title: string;
  message: string;
  url?: string;
  urlTitle?: string;
}): Promise<boolean> {
  const token = process.env.PUSHOVER_TOKEN;
  const user = process.env.PUSHOVER_USER;
  if (!token || !user) return false;
  try {
    const body = new URLSearchParams({
      token,
      user,
      title: params.title,
      message: params.message,
    });
    if (params.url) body.set("url", params.url);
    if (params.urlTitle) body.set("url_title", params.urlTitle);
    const res = await fetch(PUSHOVER_URL, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Page the operator when a demo signup fails to receive a trial. Silence here
 * is what let the 2026-08-13 outage run for 22 days.
 */
export async function notifyDemoProvisioningFailure(reason: string): Promise<void> {
  await sendPushover({
    title: "radon demo provisioning failed",
    message: `A demo.radon.run signup was not granted a trial: ${reason}`,
    url: "https://demo.radon.run/sign-up",
    urlTitle: "demo sign-up",
  });
}
