import { describe, expect, it } from "vitest";
import { pickReleaseSha, resolveReleaseIdentity } from "../scripts/release-identity.mjs";

describe("resolveReleaseIdentity", () => {
  it("stamps a production build and leaves a dev build local", () => {
    expect(resolveReleaseIdentity({
      version: "0.8.0",
      sha: "abc123def456",
      nodeEnv: "production",
      now: "2026-10-09T18:00:00Z",
    })).toEqual({
      version: "0.8.0",
      sha: "abc123def456",
      builtAt: "2026-10-09T18:00:00Z",
      channel: "production",
    });

    expect(resolveReleaseIdentity({
      version: "0.8.0",
      sha: "abc123def456",
      nodeEnv: "development",
      now: "2026-10-09T18:00:00Z",
    })).toMatchObject({ channel: "local", builtAt: "", sha: "abc123def456" });
  });

  it("uses an explicit sha when the image build has no git directory", () => {
    expect(pickReleaseSha({
      override: "87a702a76ec4",
      gitSha: "",
      githubSha: "",
    })).toBe("87a702a76ec4");
    expect(pickReleaseSha({
      override: "",
      gitSha: "",
      githubSha: "5e78d84f32b1aff9e375e05ae0961b84928185e5",
    })).toBe("5e78d84f32b1");
    expect(pickReleaseSha({
      override: "not-a-sha",
      gitSha: "abc123def456",
      githubSha: "5e78d84f32b1aff9e375e05ae0961b84928185e5",
    })).toBe("abc123def456");
  });

  it("refuses to call an unknown sha a production deploy", () => {
    expect(resolveReleaseIdentity({
      version: "0.8.0",
      sha: "",
      nodeEnv: "production",
      now: "2026-10-09T18:00:00Z",
    })).toMatchObject({ channel: "local", sha: "unknown", builtAt: "" });
  });
});
