/**
 * vitest.setup.ts runs before every test file. About 700 of the ~1,180 files
 * run in the default node environment, where nothing renders and the global
 * @testing-library cleanup has nothing to do. Loading @testing-library/react
 * there (and with it react-dom and @testing-library/dom) was pure per-file
 * setup cost on every CI shard. The setup now imports it only when a DOM
 * exists; this node-environment file proves the setup did not load it.
 *
 * RTL ships CommonJS and vitest externalizes node_modules, so loading it
 * populates Node's require.cache. Each test file runs in a fresh worker
 * (isolate is on), so the cache reflects this file and its setup only.
 */
import { createRequire } from "node:module";
import { describe, it, expect } from "vitest";
import { setMatchMedia } from "../../vitest.setup";

const require = createRequire(import.meta.url);

describe("vitest.setup.ts in the node environment", () => {
  it("has no DOM", () => {
    expect(typeof document).toBe("undefined");
    expect(typeof window).toBe("undefined");
  });

  it("does not load @testing-library/react", () => {
    const loaded = Object.keys(require.cache).filter((id) =>
      /[\\/]node_modules[\\/]@testing-library[\\/]react[\\/]/.test(id),
    );
    expect(loaded).toEqual([]);
  });

  it("still exports setMatchMedia for files that import the setup module", () => {
    expect(typeof setMatchMedia).toBe("function");
    expect(() => setMatchMedia(false)).not.toThrow();
  });
});
