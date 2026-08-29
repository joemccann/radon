import { describe, expect, it } from "vitest";

import { buildUrl, parseArgs } from "./cli.js";
import { DEFAULT_URL } from "./protocol.js";

describe("parseArgs", () => {
  it("defaults to the English public feed, JSONL, heartbeats off", () => {
    expect(parseArgs([])).toEqual({
      url: DEFAULT_URL,
      lang: "en",
      all: false,
      pretty: false,
      seconds: null,
      max: null,
      serve: false,
      port: null,
    });
  });

  it("accepts --pretty --all --seconds --max --lang", () => {
    const opts = parseArgs([
      "--pretty",
      "--all",
      "--seconds",
      "15",
      "--max",
      "3",
      "--lang",
      "zh",
    ]);
    expect(opts.pretty).toBe(true);
    expect(opts.all).toBe(true);
    expect(opts.seconds).toBe(15);
    expect(opts.max).toBe(3);
    expect(opts.lang).toBe("zh");
    expect(opts.url).toBe("wss://api.mktnews.net/?lang=zh");
  });

  it("lets --url override lang", () => {
    const opts = parseArgs(["--url", "wss://example.test/ws", "--lang", "zh"]);
    expect(opts.url).toBe("wss://example.test/ws");
  });
});

describe("buildUrl", () => {
  it("appends lang to the default host", () => {
    expect(buildUrl({ lang: "en" })).toBe(DEFAULT_URL);
    expect(buildUrl({ lang: "zh" })).toBe("wss://api.mktnews.net/?lang=zh");
  });
});
