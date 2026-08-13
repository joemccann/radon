import { describe, expect, it } from "vitest";

import { scrubSecrets } from "@/lib/apiContracts";

describe("scrubSecrets comprehensive formats", () => {
  it.each([
    'Authorization: Bearer opaque-secret-value',
    '"auth_token":"opaque-secret-value"',
    'access_token=opaque-secret-value',
    'client_secret: opaque-secret-value',
    'password="opaque-secret-value"',
    'https://provider.test/path?api_key=opaque-secret-value&x=1',
    'account DU7654321 rejected',
  ])("removes %s", (raw) => {
    const scrubbed = scrubSecrets(raw);
    expect(scrubbed).not.toContain("opaque-secret-value");
    expect(scrubbed).not.toContain("DU7654321");
    expect(scrubbed).toContain("[redacted");
  });
});
