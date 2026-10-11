import { describe, expect, it } from "vitest";
import { pickReleaseSha, pickReleaseVersion, resolveReleaseIdentity } from "../scripts/release-identity.mjs";

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

describe("pickReleaseVersion", () => {
  it("prefers the version the image build derived from history", () => {
    expect(pickReleaseVersion({ override: "0.12.3", history: () => "0.12.1", packageVersion: "0.7.0" })).toBe("0.12.3");
  });

  it("derives from history before trusting a committed package version", () => {
    expect(pickReleaseVersion({ override: "", history: () => "0.12.1", packageVersion: "0.7.0" })).toBe("0.12.1");
    expect(pickReleaseVersion({ override: "not-semver", history: () => "0.12.1\n", packageVersion: "0.7.0" })).toBe("0.12.1");
  });

  it("does not run history when the override is valid", () => {
    const history = () => {
      throw new Error("no history in the image");
    };
    expect(pickReleaseVersion({ override: "1.0.0", history, packageVersion: "0.7.0" })).toBe("1.0.0");
  });

  it("falls back to the package version when history is unavailable", () => {
    const history = () => {
      throw new Error("no history");
    };
    expect(pickReleaseVersion({ override: undefined, history, packageVersion: "0.9.0" })).toBe("0.9.0");
    expect(pickReleaseVersion({ override: "", history: () => "garbage", packageVersion: "0.9.0" })).toBe("0.9.0");
  });
});
