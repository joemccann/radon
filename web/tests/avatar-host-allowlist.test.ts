import { describe, expect, it } from "vitest";

import { ALLOWED_IMAGE_HOSTS, isAllowedImageUrl } from "../lib/imageHosts";
import { buildCspWithNonce } from "../middleware";

// Claude Security F9 tightened CSP img-src off its bare `https:` wildcard so an
// injected markdown image in an assistant answer cannot beacon account figures
// to an attacker host. That left the profile avatar validator one clause wider
// than the CSP: it still accepted ANY https URL, so a stored or newly-PUT
// avatar on some third host was accepted by the API and then silently blocked
// by the browser. These pins keep the two definitions from drifting apart
// again in either direction.
describe("avatar host allowlist", () => {
  it("accepts the hosts the app actually serves images from", () => {
    expect(isAllowedImageUrl("https://media.radon.run/avatars/a.png")).toBe(true);
    expect(isAllowedImageUrl("https://img.clerk.com/x.png")).toBe(true);
  });

  it("accepts inline data: avatars, which never leave the browser", () => {
    expect(isAllowedImageUrl("data:image/png;base64,iVBORw0KGgo=")).toBe(true);
  });

  it("rejects an arbitrary https host, the exfiltration shape F9 closed", () => {
    expect(isAllowedImageUrl("https://attacker.example/p?d=netliq")).toBe(false);
  });

  it("rejects a lookalike host that merely ends with an allowed name", () => {
    expect(isAllowedImageUrl("https://evil-media.radon.run.attacker.example/x")).toBe(false);
    expect(isAllowedImageUrl("https://notmedia.radon.run/x")).toBe(false);
  });

  it("rejects non-https schemes", () => {
    expect(isAllowedImageUrl("http://media.radon.run/x.png")).toBe(false);
    expect(isAllowedImageUrl("javascript:alert(1)")).toBe(false);
  });

  it("rejects unparseable input rather than passing it through", () => {
    expect(isAllowedImageUrl("https://")).toBe(false);
    expect(isAllowedImageUrl("")).toBe(false);
  });

  it("is the same list the CSP img-src directive is built from", () => {
    const imgSrc = buildCspWithNonce("test-nonce")
      .split(";")
      .map((directive) => directive.trim())
      .find((directive) => directive.startsWith("img-src"));
    expect(imgSrc).toBeDefined();
    for (const host of ALLOWED_IMAGE_HOSTS) {
      expect(
        imgSrc!.includes(`https://${host}`),
        `img-src does not cover ${host}, so an avatar the API accepts would be blocked`,
      ).toBe(true);
    }
    expect(
      /img-src[^;]*\shttps:(\s|$)/.test(imgSrc!),
      "img-src is a bare https: wildcard again, which reopens the F9 exfiltration path",
    ).toBe(false);
  });
});
